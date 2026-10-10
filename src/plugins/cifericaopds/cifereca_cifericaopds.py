"""CifericaOPDS: static, offline-first experimental provider.collection pilot.

The real upstream OpenAPI v0.2 is not available: candidate_v02 routes and JSON
are explicitly NON-NORMATIVE. No network access at activate()/snapshot().
Only cifereca.plugin_api is imported; no private registry access or remote code.
"""
from __future__ import annotations

from dataclasses import asdict
import json
import os
from pathlib import Path
from threading import RLock
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from cifereca.plugin_api import (
    ProviderCapabilities, ProviderDescriptor, ProviderPlugin, ProviderSession,
    SearchResult, ProviderCollectionSnapshot, validate_provider_collection_snapshot,
)

PARENT_ID = "org.cifereca.integration.cifericaopds"
MAX_JSON_BYTES = 1024 * 1024
MAX_RESULTS = 500


class ContractUnavailable(RuntimeError):
    """Unavailable, invalid or unverified server contract."""


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise ContractUnavailable("redirect refused")


def safe_base_url(raw: str) -> str:
    """Reject credentials, fragments, arbitrary paths and plaintext off loopback."""
    try:
        parts = urlsplit(str(raw).strip())
        if (parts.scheme not in ("http", "https") or not parts.hostname
                or parts.username or parts.password or parts.query or parts.fragment
                or parts.path not in ("", "/")):
            raise ValueError("invalid origin")
        port = parts.port
        hostname = parts.hostname.lower()
    except (ValueError, TypeError) as exc:
        raise ContractUnavailable("invalid configured origin") from exc
    if parts.scheme == "http" and hostname not in ("localhost", "127.0.0.1", "::1"):
        raise ContractUnavailable("HTTP is allowed only on loopback")
    host = f"[{hostname}]" if ":" in hostname else hostname
    return urlunsplit((parts.scheme, host + (f":{port}" if port is not None else ""), "", "", ""))


class JsonClient:
    def __init__(self, base_url, timeout=4):
        self.base_url = safe_base_url(base_url)
        self.timeout = min(20.0, max(0.5, float(timeout)))
        self._opener = build_opener(NoRedirects())

    def get(self, endpoint: str, *, params=None):
        if not endpoint.startswith("/api/v1/") or "?" in endpoint or "#" in endpoint:
            raise ContractUnavailable("endpoint not allowed")
        url = self.base_url + endpoint + ("?" + urlencode(params) if params else "")
        request = Request(url, headers={
            "Accept": "application/json", "User-Agent": "cifereca-cifericaopds/0.1"
        })
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                if response.status != 200:
                    raise ContractUnavailable("unexpected HTTP status")
                content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip()
                if content_type != "application/json":
                    raise ContractUnavailable("invalid JSON content type")
                raw = response.read(MAX_JSON_BYTES + 1)
        except (HTTPError, URLError, OSError, TimeoutError) as exc:
            raise ContractUnavailable("server unavailable") from exc
        if len(raw) > MAX_JSON_BYTES:
            raise ContractUnavailable("oversized JSON response")
        try:
            result = json.loads(raw)
        except (ValueError, UnicodeDecodeError) as exc:
            raise ContractUnavailable("invalid server JSON") from exc
        if not isinstance(result, dict):
            raise ContractUnavailable("JSON object required")
        return result


def _text(value, label, maximum=512):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ContractUnavailable(f"invalid {label}")
    return value.strip()


def candidate_inventory(data, previous, *, show_synthetic=False):
    """Candidate *fixture* schema; Core validates again before publication."""
    major, generation = data.get("api_major"), data.get("discovery_generation")
    if type(major) is not int or major != 1:
        raise ContractUnavailable("unsupported candidate API major")
    if type(generation) is not int or generation < 0:
        raise ContractUnavailable("invalid candidate discovery generation")
    sources = data.get("sources")
    if not isinstance(sources, list) or len(sources) > 128:
        raise ContractUnavailable("invalid source list")
    children = []
    for source in sources:
        if not isinstance(source, dict):
            raise ContractUnavailable("invalid source")
        sid = _text(source.get("source_id"), "source_id", 64)
        kind = _text(source.get("kind"), "kind", 128)
        if kind == "synthetic.merged" and not show_synthetic:
            continue
        caps = source.get("capabilities")
        if not isinstance(caps, list) or any(not isinstance(x, str) for x in caps):
            raise ContractUnavailable("invalid capabilities")
        # No assumption of downloads, loans, authentication or SSE support.
        safe_caps = [x for x in caps if x in ("catalog.search", "metadata.enrich")]
        status = _text(source.get("status"), "status", 32)
        if status not in ("ready", "disabled", "removed", "degraded"):
            raise ContractUnavailable("unsupported source status")
        revision = _text(source.get("source_revision", source.get("revision")),
                         "source_revision", 128)
        catalog = source.get("catalog_generation", generation)
        if type(catalog) is not int or catalog < 0:
            raise ContractUnavailable("invalid catalog generation")
        children.append({
            "plugin_id": f"cifericaopds.{sid}", "source_id": sid,
            "capabilities": safe_caps, "source_revision": revision,
            "catalog_generation": catalog, "status": status,
            "kind": kind, "instantiation": "singleton_public",
            "parent_plugin_id": PARENT_ID, "is_virtual": True,
        })
    return validate_provider_collection_snapshot({
        "api_major": 1, "discovery_generation": generation,
        "parent_plugin_id": PARENT_ID, "children": children,
    }, parent_plugin_id=PARENT_ID, previous=previous)


def candidate_results(data, provider_id):
    """Conservative projection: no inferred rights, signed URLs or user data."""
    values = data.get("items")
    if not isinstance(values, list) or len(values) > MAX_RESULTS:
        raise ContractUnavailable("invalid candidate item list")
    rows, seen = [], set()
    for item in values:
        if not isinstance(item, dict):
            raise ContractUnavailable("invalid candidate item")
        record_id = _text(item.get("id"), "record id", 256)
        title = _text(item.get("title"), "title", 1024)
        if record_id in seen:
            continue
        seen.add(record_id)
        author = item.get("author")
        if author is not None and (not isinstance(author, str) or len(author) > 512):
            raise ContractUnavailable("invalid author")
        rows.append(SearchResult(
            id=f"{provider_id}:{record_id}", record_id=record_id,
            docbase="CIFERICAOPDS", medium="unknown", title=title,
            author=author, type=None, format=None, publisher=None,
            published_at=None, description=None, thumbnail_url=None,
            friendly_url=None, scenario="CATALOG",
            source_provider=provider_id, source_credential="__public__",
            metadata={"source_id": provider_id.removeprefix("cifericaopds.")},
        ))
    return rows


class CifericaSession(ProviderSession):
    def __init__(self, *, client, mode, directory, **kw):
        super().__init__(**kw)
        self._client, self._mode, self._directory = client, mode, directory

    def connect(self):
        self.connected = True  # No discovery or network probe.
        return []

    def close(self):
        self.connected = False

    def search(self, query, scope="all", page=0, field="all"):
        sid = self.provider_id.removeprefix("cifericaopds.")
        if self._mode == "fixture":
            path = self._directory / "fixture-results.json"
            if not path.exists():
                return []
            if path.stat().st_size > MAX_JSON_BYTES:
                raise ContractUnavailable("oversized local results fixture")
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not isinstance(data.get(sid, {"items": []}), dict):
                raise ContractUnavailable("invalid local results fixture")
            values = candidate_results(data.get(sid, {"items": []}), self.provider_id)
            q = str(query).strip().casefold()
            return [x for x in values if q in x.title.casefold() or q in (x.author or "").casefold()]
        # Explicit user search only. This GET schema is UNVERIFIED for real v0.2.
        data = self._client.get("/api/v1/search", params={"source_id": sid, "q": str(query)})
        return candidate_results(data, self.provider_id)


class CifericaProvider(ProviderPlugin):
    def __init__(self, source_id, capabilities, *, client, mode, directory):
        self._client, self._mode, self._directory = client, mode, directory
        self.descriptor = ProviderDescriptor(
            id=f"cifericaopds.{source_id}", display_name=f"CifericaOPDS · {source_id}",
            description="Public metadata only; no acquisition rights",
            state="ready",
            capabilities=ProviderCapabilities(search="catalog.search" in capabilities),
        )

    def create_session(self, *, credential_id, label, credentials, options,
                       config, storage, diagnostics):
        if credentials:
            raise ContractUnavailable("virtual sources cannot hold credentials")
        return CifericaSession(
            descriptor=self.descriptor, credential_id=credential_id,
            label=label, credentials={}, options={},
            client=self._client, mode=self._mode, directory=self._directory,
        )


class CifericaCollection:
    def __init__(self):
        self._lock = RLock()
        self._snapshot = None
        self._callbacks = []
        self._directory = None
        self._client = None
        self._mode = "fixture"
        self._show_synthetic = False

    def activate(self, context):
        cfg = dict(context.config)
        self._mode = cfg.get("mode", "fixture")
        if self._mode not in ("fixture", "candidate_v02"):
            raise ContractUnavailable("unknown plugin mode")
        self._client = JsonClient(cfg.get("base_url", "http://127.0.0.1:8788"),
                                  cfg.get("timeout_seconds", 4))
        self._show_synthetic = bool(cfg.get("show_synthetic", False))
        self._directory = context.data_directory
        path = self._directory / "inventory.json"
        if path.is_file() and path.stat().st_size <= MAX_JSON_BYTES:
            try:
                self._snapshot = validate_provider_collection_snapshot(
                    json.loads(path.read_text(encoding="utf-8")),
                    parent_plugin_id=PARENT_ID,
                )
            except (ValueError, TypeError, OSError):
                self._snapshot = None
        return self

    def deactivate(self):
        with self._lock:
            self._callbacks.clear()

    def snapshot(self):
        with self._lock:
            if self._snapshot is None:
                raise ContractUnavailable("no validated inventory; explicit refresh required")
            return self._snapshot

    def refresh(self, *, explicit=True):
        if explicit is not True:
            raise ContractUnavailable("discovery must be explicit")
        if self._directory is None:
            raise ContractUnavailable("plugin is not active")
        if self._mode == "fixture":
            path = self._directory / "fixture-sources.json"
            if path.stat().st_size > MAX_JSON_BYTES:
                raise ContractUnavailable("oversized local source fixture")
            data = json.loads(path.read_text(encoding="utf-8"))
        else:
            # Experimental candidate route, not an assertion about upstream v0.2.
            data = self._client.get("/api/v1/sources")
        if not isinstance(data, dict):
            raise ContractUnavailable("invalid candidate inventory")
        with self._lock:
            accepted = candidate_inventory(data, self._snapshot,
                                           show_synthetic=self._show_synthetic)
            if accepted == self._snapshot:
                return accepted
            path = self._directory / "inventory.json"
            temp = path.with_suffix(".tmp")
            try:
                temp.write_text(json.dumps(asdict(accepted), ensure_ascii=False,
                                           sort_keys=True), encoding="utf-8")
                os.replace(temp, path)
            finally:
                if temp.exists():
                    temp.unlink()
            self._snapshot = accepted
            subscribers = tuple(self._callbacks)
        for callback in subscribers:
            try:
                callback()
            except Exception:
                pass
        return accepted

    def create_provider(self, source_id):
        with self._lock:
            snapshot = self._snapshot
        if snapshot is None:
            raise ContractUnavailable("no local source inventory")
        child = next((row for row in snapshot.children
                      if row.source_id == source_id and row.status == "ready"), None)
        if child is None:
            raise ContractUnavailable("source absent or disabled")
        return CifericaProvider(source_id, child.capabilities,
                                client=self._client, mode=self._mode,
                                directory=self._directory)

    def subscribe(self, callback):
        with self._lock:
            self._callbacks.append(callback)
        def unsubscribe():
            with self._lock:
                if callback in self._callbacks:
                    self._callbacks.remove(callback)
        return unsubscribe


PLUGIN = CifericaCollection()
