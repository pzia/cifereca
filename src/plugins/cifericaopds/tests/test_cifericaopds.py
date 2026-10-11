"""#969 experimental fixture contract; no real v0.2 HTTP compatibility claim."""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from types import SimpleNamespace as NS

import pytest

from cifereca.plugin_api import ProviderCollection, ProviderPlugin
from cifereca_cifericaopds import (
    CifericaCollection, ContractUnavailable, PARENT_ID, candidate_inventory,
    candidate_results, safe_base_url,
)


def inventory(*ids, generation=1):
    return {
        "api_major": 1, "discovery_generation": generation,
        "sources": [{
            "source_id": sid, "revision": f"v{generation}",
            "catalog_generation": generation, "kind": "catalog",
            "status": "ready", "capabilities": ["catalog.search"],
        } for sid in ids],
    }


def context(directory, **overrides):
    directory.mkdir(exist_ok=True)
    return NS(data_directory=directory, config={
        "mode": "fixture", "base_url": "http://127.0.0.1:8788", **overrides,
    })


def test_package_and_public_contract():
    pkg = Path(__file__).resolve().parents[1]
    manifest = json.loads((pkg / "cifereca-plugin.json").read_text())
    assert manifest["package"]["id"] == PARENT_ID
    assert manifest["plugins"][0]["type"] == "provider.collection"
    assert manifest["plugins"][0]["enabled_by_default"] is False
    assert isinstance(CifericaCollection(), ProviderCollection)


def test_two_virtual_sources_and_restart_cache_without_network(tmp_path):
    ctx = context(tmp_path)
    (tmp_path / "fixture-sources.json").write_text(json.dumps(inventory("alpha", "beta")))
    (tmp_path / "fixture-results.json").write_text(json.dumps({
        "alpha": {"items": [{"id": "1", "title": "Livre Alpha"}]},
        "beta": {"items": [{"id": "2", "title": "Livre Beta"}]},
    }))
    collection = CifericaCollection().activate(ctx)
    with pytest.raises(ContractUnavailable):
        collection.snapshot()
    assert collection.refresh().discovery_generation == 1
    assert {e.plugin_id for e in collection.snapshot().children} == {
        "cifericaopds.alpha", "cifericaopds.beta",
    }
    def session(sid):
        plugin = collection.create_provider(sid)
        assert isinstance(plugin, ProviderPlugin)
        result = plugin.create_session(
            credential_id="__public__", label=sid, credentials={}, options={},
            config=None, storage=None, diagnostics=None,
        )
        result.connect()
        return result
    a, b = session("alpha"), session("beta")
    assert {x.source_provider for x in a.search("Livre")} == {"cifericaopds.alpha"}
    assert {x.source_provider for x in b.search("Livre")} == {"cifericaopds.beta"}
    assert a.search("nom absent") == []
    a.close(); b.close()
    offline = CifericaCollection().activate(ctx)
    assert offline.snapshot() == collection.snapshot()
    assert offline.create_provider("alpha").descriptor.id == "cifericaopds.alpha"


def test_notifications_and_atomic_rollback(tmp_path):
    coll = CifericaCollection().activate(context(tmp_path))
    fixture = tmp_path / "fixture-sources.json"
    fixture.write_text(json.dumps(inventory("alpha", "beta")))
    observed = []
    unsub = coll.subscribe(lambda: observed.append(coll.snapshot().discovery_generation))
    coll.refresh()
    coll.refresh()
    assert observed == [1]
    checkpoint = (tmp_path / "inventory.json").read_bytes()
    fixture.write_text('{"api_major":2,"sources":[]}')
    with pytest.raises(ContractUnavailable):
        coll.refresh()
    assert observed == [1]
    assert (tmp_path / "inventory.json").read_bytes() == checkpoint
    fixture.write_text(json.dumps(inventory("alpha", "gamma", generation=2)))
    coll.refresh()
    assert observed == [1, 2]
    assert coll.create_provider("gamma").descriptor.id == "cifericaopds.gamma"
    with pytest.raises(ContractUnavailable):
        coll.create_provider("beta")
    unsub()


def test_synthetic_filter_and_nonsearchable_source(tmp_path):
    data = inventory("alpha", "merged")
    data["sources"][1]["kind"] = "synthetic.merged"
    data["sources"].append({
        "source_id": "metadata", "revision": "v1", "kind": "catalog",
        "status": "disabled", "capabilities": ["metadata.enrich"],
    })
    only = candidate_inventory(data, None)
    assert {c.source_id for c in only.children} == {"alpha", "metadata"}
    expanded = candidate_inventory(data, None, show_synthetic=True)
    assert {c.source_id for c in expanded.children} == {"alpha", "metadata", "merged"}
    (tmp_path / "fixture-sources.json").write_text(json.dumps(data))
    coll = CifericaCollection().activate(context(tmp_path, show_synthetic=True))
    coll.refresh()
    with pytest.raises(ContractUnavailable):
        coll.create_provider("metadata")


def test_candidate_metadata_must_not_grant_acquisition():
    results = candidate_results({
        "items": [{"id": "r1", "title": "Livre", "owned": True,
                   "borrowable": True, "download_url": "https://bad.invalid/signed",
                   "access_token": "secret", "author": "Auteur"}],
    }, "cifericaopds.alpha")
    assert len(results) == 1
    assert results[0].medium == "unknown"
    assert results[0].source_credential == "__public__"
    assert not results[0].owned
    assert results[0].friendly_url is None
    assert "secret" not in str(results[0].metadata)


@pytest.mark.parametrize("origin", [
    "http://example.com", "http://user:pass@localhost:8788",
    "http://127.0.0.1:bad", "https://example.com/path",
    "https://example.com?bearer=secret", "file:///tmp/payload",
])
def test_strict_origin_allowlist(origin):
    with pytest.raises(ContractUnavailable):
        safe_base_url(origin)


def test_https_or_loopback():
    assert safe_base_url("http://127.0.0.1:8788") == "http://127.0.0.1:8788"
    assert safe_base_url("https://opds.example.org/") == "https://opds.example.org"


@pytest.fixture
def mocked_candidate_server():
    calls = []
    state = {"bad": False, "redirect": False}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            calls.append(self.path)
            if state["redirect"]:
                self.send_response(302)
                self.send_header("Location", "http://127.0.0.1:1/token")
                self.end_headers()
                return
            if state["bad"]:
                self.send_response(503)
                self.end_headers()
                return
            data = (inventory("alpha", "beta") if self.path == "/api/v1/sources"
                    else {"items": [{"id": "book", "title": "Livre HTTP"}]})
            payload = json.dumps(data).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", calls, state
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)


def test_candidate_http_is_explicit_and_fails_closed(tmp_path, mocked_candidate_server):
    url, calls, state = mocked_candidate_server
    collection = CifericaCollection().activate(context(
        tmp_path, mode="candidate_v02", base_url=url,
    ))
    assert calls == []
    with pytest.raises(ContractUnavailable):
        collection.snapshot()
    collection.refresh()
    assert calls == ["/api/v1/sources"]
    assert len(collection.snapshot().children) == 2
    provider = collection.create_provider("alpha")
    session = provider.create_session(
        credential_id="__public__", label="alpha", credentials={},
        options={}, config=None, storage=None, diagnostics=None,
    )
    session.connect()
    assert len(session.search("Livre")) == 1
    assert len(calls) == 2
    assert calls[1].startswith("/api/v1/search?source_id=alpha&q=Livre")
    known_good = collection.snapshot()
    state["bad"] = True
    with pytest.raises(ContractUnavailable):
        collection.refresh()
    assert collection.snapshot() == known_good
    state["bad"] = False
    state["redirect"] = True
    with pytest.raises(ContractUnavailable):
        collection.refresh()
    assert collection.snapshot() == known_good
    session.close()


def test_invalid_child_ids_rejected_without_remote_code_execution():
    for sid in ("ALPHA", "nested/name", "a.b"):
        with pytest.raises((ValueError, ContractUnavailable)):
            candidate_inventory(inventory(sid), None)
    data = inventory("alpha")
    data["sources"][0]["capabilities"] = ["catalog.search", "acquisition.download"]
    result = candidate_inventory(data, None)
    assert result.children[0].capabilities == ("catalog.search",)



def test_attested_public_bibliography_is_projected_without_untrusted_links():
    row = candidate_results({"items": [{
        "id": "catalogue-1", "title": "Un livre", "author": "Auteur",
        "publisher": "Éditions exemple", "type": "roman", "format": "epub",
        "published_at": "2026-09-22", "language": "fr",
        "isbn13": "9782211348171",
        "description": "Un résumé contenant un compte privé",
        "thumbnail_url": "https://invalid.example/signed?token=secret",
        "friendly_url": "https://invalid.example/signed?token=secret",
        "access_token": "secret", "download_url": "https://invalid.example/secret",
        "owned": True, "downloadable": True,
    }]}, "cifericaopds.alpha")[0]
    assert (row.publisher, row.type, row.format, row.published_at) == (
        "Éditions exemple", "roman", "epub", "2026-09-22",
    )
    assert row.metadata == {
        "source_id": "alpha", "isbn": "9782211348171", "language": "fr",
    }
    assert row.description is None
    assert row.thumbnail_url is None and row.friendly_url is None
    assert row.download_url is None and row.owned is None and row.downloadable is None
    assert "secret" not in str(row.metadata)


def test_non_bookland_and_untrusted_optional_fields_are_never_promoted():
    rows = candidate_results({"items": [{
        "id": "1", "title": "Livre",
        "isbn13": "3127020510184",
        "publisher": "https://publisher.invalid/private?token=abc",
        "published_at": "2026-02-30",
        "language": "a::b", "format": "access_token=abc",
        "type": ["roman"],
    }]}, "cifericaopds.alpha")
    assert len(rows) == 1
    row = rows[0]
    assert row.publisher is None and row.published_at is None
    assert row.format is None and row.type is None
    assert "isbn" not in row.metadata and "language" not in row.metadata


def test_unverified_pagination_never_duplicates_first_page(tmp_path):
    (tmp_path / "fixture-sources.json").write_text(json.dumps(inventory("alpha")))
    (tmp_path / "fixture-results.json").write_text(json.dumps({
        "alpha": {"items": [{"id": "1", "title": "Premier livre"}]},
    }))
    coll = CifericaCollection().activate(context(tmp_path))
    coll.refresh()
    session = coll.create_provider("alpha").create_session(
        credential_id="__public__", label="alpha", credentials={},
        options={}, config=None, storage=None, diagnostics=None,
    )
    assert len(session.search("Premier", page=0)) == 1
    assert session.search("Premier", page=1) == []
    with pytest.raises(ContractUnavailable):
        session.search("Premier", page=-1)
    with pytest.raises(ContractUnavailable):
        session.search("Premier", page="1")
    session.close()
