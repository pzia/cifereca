"""Core #968 HTTP boundary smoke with a DETERMINISTIC, NON-NORMATIVE server.

The actual CifericaOPDS 0.2.0 OpenAPI is NOT bundled here. The JSON shape in
this fake server deliberately tests the Core/provider.collection boundary,
not conformance of the separately owned Plugins #969 HTTP client to v0.2.0.
Only a local Python test double translates HTTP data into the public API.
No production plugin/network client is created in this file.
"""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock, Thread
from types import SimpleNamespace as NS
from urllib.parse import parse_qs, urlsplit
from urllib.request import urlopen

import pytest

from cifereca.config import Config
from cifereca.diagnostics import Diagnostics
from cifereca.models import SearchResult
from cifereca.plugin_api import validate_provider_collection_snapshot
from cifereca.providers import (
    ProviderCapabilities, ProviderDescriptor, ProviderPlugin, ProviderSession,
)
from cifereca.services import LibraryService
from cifereca.storage import Storage

PARENT = "org.cifereca.integration.cifericaopds"


def _source(source_id, *, revision="1", status="ready", kind="catalog", searchable=True):
    return {
        "source_id": source_id,
        "revision": revision,
        "status": status,
        "kind": kind,
        "capabilities": ["catalog.search"] if searchable else ["metadata.enrich"],
    }


class _HttpScenario:
    def __init__(self):
        self.lock = Lock()
        self.generation = 1
        self.sources = [_source("alpha"), _source("beta")]
        self.failure = ""  # 503, malformed JSON, unsupported major
        self.requests = []

    def record(self, route, query):
        with self.lock:
            self.requests.append((route, query))

    def count(self, route):
        with self.lock:
            return sum(1 for item in self.requests if item[0] == route)

    def search_sources(self):
        with self.lock:
            return [q["source_id"][0] for route, q in self.requests
                    if route == "/api/v1/search" and "source_id" in q]


@pytest.fixture
def http_scenario():
    """Only 127.0.0.1 and an OS-assigned port; no external network required."""
    scenario = _HttpScenario()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, code, body):
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            parsed = urlsplit(self.path)
            query = parse_qs(parsed.query)
            scenario.record(parsed.path, query)
            if parsed.path == "/api/v1/sources":
                if scenario.failure == "503":
                    return self._send(503, b'{"error":"unavailable"}')
                if scenario.failure == "malformed":
                    return self._send(200, b'{"invalid":')
                with scenario.lock:
                    data = {
                        "api_major": 2 if scenario.failure == "major" else 1,
                        "discovery_generation": scenario.generation,
                        "sources": list(scenario.sources),
                    }
                return self._send(200, json.dumps(data).encode())
            if parsed.path == "/api/v1/search":
                source_id = (query.get("source_id") or [""])[0]
                if scenario.failure == "503":
                    return self._send(503, b'{"error":"unavailable"}')
                with scenario.lock:
                    allowed = any(s["source_id"] == source_id and s["status"] == "ready"
                                  and "catalog.search" in s["capabilities"]
                                  for s in scenario.sources)
                if not allowed:
                    return self._send(404, b'{"error":"unknown source"}')
                payload = {"items": [{
                    "id": "book-1", "title": f"Book from {source_id}",
                    "author": "Fixture Author",
                }]}
                return self._send(200, json.dumps(payload).encode())
            return self._send(404, b'{"error":"unknown route"}')

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield scenario, f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)


class _HttpSession(ProviderSession):
    """Local *test double*; actual transport policy belongs to Plugins #969."""

    def __init__(self, *, base_url, **kw):
        super().__init__(**kw)
        self.base_url = base_url

    def connect(self):
        # Connection is local; never discover or probe HTTP during attach.
        self.connected = True
        return []

    def close(self):
        self.connected = False

    def search(self, query, scope="all", page=0, field="all"):
        from urllib.parse import urlencode
        url = self.base_url + "/api/v1/search?" + urlencode({
            "source_id": self.provider_id.split(".", 1)[1], "q": query,
        })
        with urlopen(url, timeout=0.8) as response:
            rows = json.load(response)["items"]
        return [
            SearchResult(
                id=f"{self.provider_id}:{item['id']}", record_id=item["id"],
                docbase="FIXTURE", medium="digital", title=item["title"],
                author=item.get("author"), type="ebook", format="EPUB",
                publisher=None, published_at=None, description=None,
                thumbnail_url=None, friendly_url=None, scenario="MOCK",
                source_provider=self.provider_id, source_credential=self.credential_id,
                metadata={"source_id": self.provider_id},
            )
            for item in rows
        ]


class _HttpProvider(ProviderPlugin):
    def __init__(self, base_url, source_id, *, capabilities=()):
        self.base_url = base_url
        self.descriptor = ProviderDescriptor(
            id="cifericaopds." + source_id,
            display_name=f"HTTP fixture {source_id}",
            description="Offline API boundary simulation", state="ready",
            capabilities=ProviderCapabilities(search="catalog.search" in capabilities),
        )

    def create_session(self, *, credential_id, label, credentials, options,
                       config, storage, diagnostics):
        assert not credentials
        return _HttpSession(
            base_url=self.base_url, descriptor=self.descriptor,
            credential_id=credential_id, label=label, credentials={}, options={},
        )


class _HttpCollection:
    """Cached-only inventory plus an EXPLICIT simulated discovery operation."""

    def __init__(self, base_url):
        self.base_url = base_url
        self.cached = None
        self.callbacks = []
        self.created = []

    def snapshot(self):
        if self.cached is None:
            raise ConnectionError("no cached inventory yet")
        return self.cached

    def refresh(self, *, explicit=True):
        if not explicit:
            raise AssertionError("discovery requires an explicit call")
        with urlopen(self.base_url + "/api/v1/sources", timeout=0.8) as response:
            data = json.load(response)
        if data.get("api_major") != 1:
            raise ValueError("unsupported simulated API major")
        children = []
        for source in data["sources"]:
            sid = source["source_id"]
            children.append({
                "plugin_id": "cifericaopds." + sid,
                "source_id": sid, "capabilities": source["capabilities"],
                "source_revision": source["revision"],
                "catalog_generation": data["discovery_generation"],
                "status": source["status"], "kind": source["kind"],
                "instantiation": "singleton_public",
                "parent_plugin_id": PARENT, "is_virtual": True,
            })
        proposed = {
            "api_major": 1, "discovery_generation": data["discovery_generation"],
            "parent_plugin_id": PARENT, "children": children,
        }
        # The test adapter validates before publishing, but Core validates
        # independently (including namespace, generation and collisions).
        accepted = validate_provider_collection_snapshot(
            proposed, parent_plugin_id=PARENT, previous=self.cached,
        )
        self.cached = accepted
        for callback in tuple(self.callbacks):
            callback()
        return accepted

    def create_provider(self, source_id):
        self.created.append(source_id)
        matched = next(
            (row for row in self.cached.children if row.source_id == source_id),
            None,
        ) if self.cached is not None else None
        caps = matched.capabilities if matched is not None else ("catalog.search",)
        return _HttpProvider(self.base_url, source_id, capabilities=caps)

    def subscribe(self, callback):
        self.callbacks.append(callback)

        def unsubscribe():
            if callback in self.callbacks:
                self.callbacks.remove(callback)
        return unsubscribe


class _Manager:
    def __init__(self, service):
        self.collection = service
        self.active = True
        self.disabled = set()
        self.state = NS(identity_enabled_override=lambda provider_id, identity:
                        False if (provider_id, identity) in self.disabled else None)

    def candidates(self, category):
        return [NS(descriptor=NS(id=PARENT))] if category == "provider.collection" and self.active else []

    def service(self, plugin_id):
        assert plugin_id == PARENT
        return self.collection

    def status(self, plugin_id):
        assert plugin_id == PARENT
        return NS(enabled=self.active, state="ready" if self.active else "disabled",
                  descriptor=NS(id=PARENT, instantiation="multiple"))

    def enabled_for_identity(self, plugin_id, identity):
        return self.active and plugin_id == PARENT

    def subscribe_services(self, category, callback):
        return lambda: None


def _library(tmp_path, name="cache.sqlite3"):
    config = Config(tmp_path / "config.json")
    config.set("library.directory", str(tmp_path / "library"))
    return LibraryService(config, Storage(tmp_path / name), Diagnostics())


def test_http_two_sources_and_explicit_fetch_boundary(http_scenario, tmp_path):
    server, url = http_scenario
    collection = _HttpCollection(url)
    lib = _library(tmp_path)
    try:
        assert lib.attach_plugin_manager(_Manager(collection)) == []
        lib.connect_configured()
        assert server.requests == []  # startup and adapter creation are cache-only
        collection.refresh()  # the only discovery request
        assert server.count("/api/v1/sources") == 1
        lib.refresh_virtual_collections(connect_changed=True)
        assert set(lib.registry.virtual_ids(PARENT)) == {
            "cifericaopds.alpha", "cifericaopds.beta",
        }
        assert server.count("/api/v1/search") == 0  # still no remote search
        rows = lib.search("Book", force=True)
        assert {row.source_provider for row in rows} == set(lib.registry.virtual_ids(PARENT))
        assert all(row.source_credential == "__public__" for row in rows)
        assert set(server.search_sources()) == {"alpha", "beta"}
        assert server.count("/api/v1/sources") == 1
        # Separate result sets, both reachable from the public progressive API.
        batches = []
        summary = lib.search_progressive("Book", force=True, on_batch=batches.append)
        assert summary.providers_total == 2
        assert {b.provider_id for b in batches} == set(lib.registry.virtual_ids(PARENT))
        assert all(b.error_code == "" for b in batches)
    finally:
        lib.close()


def test_http_inventory_delta_and_identity_scoping(http_scenario, tmp_path):
    server, url = http_scenario
    collection = _HttpCollection(url)
    collection.refresh()
    manager = _Manager(collection)
    lib = _library(tmp_path)
    try:
        lib.attach_plugin_manager(manager)
        lib.connect_configured()
        old = lib._sessions["cifericaopds.alpha::__public__"]
        # One source renamed, another becomes metadata-only; a third is added.
        with server.lock:
            server.generation = 2
            server.sources = [
                _source("alpha", revision="2"),  # stable provider ID
                _source("beta", revision="2", searchable=False),
                _source("gamma"),
            ]
        collection.refresh()
        lib.refresh_virtual_collections(connect_changed=True)
        assert set(lib.registry.virtual_ids(PARENT)) == {
            "cifericaopds.alpha", "cifericaopds.beta", "cifericaopds.gamma",
        }
        assert lib._sessions["cifericaopds.alpha::__public__"] is not old
        assert not old.connected
        assert "cifericaopds.beta" not in dict(lib.searchable_providers())
        assert "cifericaopds.gamma" in dict(lib.searchable_providers())
        manager.disabled.add(("cifericaopds.gamma", "default"))
        assert "cifericaopds.gamma" not in {r.source_provider for r in lib.search("Book", force=True)}
        # Explicit server tombstone and later return never erase the parent or history.
        with server.lock:
            server.generation = 3
            server.sources = [_source("alpha"), _source("beta", status="removed"),
                              _source("gamma")]
        collection.refresh()
        lib.refresh_virtual_collections(connect_changed=True)
        assert lib.registry.maybe_get("cifericaopds.beta") is None
        with server.lock:
            server.generation = 4
            server.sources = [_source("alpha"), _source("beta", revision="4"),
                              _source("gamma")]
        collection.refresh()
        lib.refresh_virtual_collections(connect_changed=True)
        assert lib.registry.maybe_get("cifericaopds.beta") is not None
        assert lib.registry.virtual_snapshot(PARENT).discovery_generation == 4
    finally:
        lib.close()


@pytest.mark.parametrize("failure", ["503", "malformed", "major"])
def test_http_error_cannot_replace_last_accepted_snapshot(http_scenario, tmp_path, failure):
    server, url = http_scenario
    collection = _HttpCollection(url)
    collection.refresh()
    lib = _library(tmp_path)
    try:
        lib.attach_plugin_manager(_Manager(collection))
        original = lib.registry.virtual_snapshot(PARENT)
        server.failure = failure
        with pytest.raises((ValueError, OSError)):
            collection.refresh()
        lib.refresh_virtual_collections()
        assert lib.registry.virtual_snapshot(PARENT) == original
        assert {x.plugin_id for x in original.children} == {
            "cifericaopds.alpha", "cifericaopds.beta",
        }
        assert server.count("/api/v1/sources") == 2  # no hidden retries
    finally:
        lib.close()


def test_http_server_unavailable_restart_retains_checkpoint_and_known_index(http_scenario, tmp_path):
    server, url = http_scenario
    collection = _HttpCollection(url)
    collection.refresh()
    lib = _library(tmp_path)
    try:
        lib.attach_plugin_manager(_Manager(collection))
        lib.connect_configured()
        assert len(lib.search("Book", force=True)) == 2
        before = lib.search_known_catalog("Book")
        assert before, "known catalogue must retain mirrored source rows"
    finally:
        lib.close()

    # New plugin process has no in-memory inventory. No HTTP read is allowed
    # during attach or local catalogue search, even when the server is down.
    server.failure = "503"
    offline = _HttpCollection(url)
    replacement = _library(tmp_path)
    calls_before = list(server.requests)
    try:
        ids = replacement.attach_plugin_manager(_Manager(offline))
        assert set(ids) == {"cifericaopds.alpha", "cifericaopds.beta"}
        assert replacement.registry.virtual_snapshot(PARENT) is not None
        assert server.requests == calls_before
        known = replacement.search_known_catalog("Book")
        assert known and {row["provider_id"] for row in known} >= set(ids)
        assert server.requests == calls_before
        # The source remains visible but is not silently queried by local read.
        assert set(p for p, _ in replacement.searchable_providers()) == set()
    finally:
        replacement.close()
