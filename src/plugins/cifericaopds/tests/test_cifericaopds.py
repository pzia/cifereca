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
    CifericaCollection, ContractUnavailable, JsonClient, PARENT_ID, candidate_inventory,
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
    state = {
        "bad": False, "redirect": False, "sources": inventory("alpha", "beta"),
        "raw": None, "content_type": "application/json", "status": 200,
    }
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
            data = (state["sources"] if self.path == "/api/v1/sources"
                    else {"items": [{"id": "book", "title": "Livre HTTP"}]})
            payload = (state["raw"] if state["raw"] is not None
                       else json.dumps(data).encode())
            self.send_response(state["status"])
            self.send_header("Content-Type", state["content_type"])
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


@pytest.mark.parametrize(("unsafe_id", "unsafe_title", "unsafe_author"), [
    ("https://example.invalid/book?token=secret", "Book", None),
    ("id/with/path", "Book", None),
    ("clean-id", "https://example.invalid/private?key=secret", None),
    ("clean-id", "A title\nwith newline", None),
    ("clean-id", "A title", "Bearer: secret"),
    ("clean-id", "A title", "author@example.invalid"),
    ("clean-id", "A title", "Author\rName"),
])
def test_candidate_records_reject_untrusted_free_text(
    unsafe_id, unsafe_title, unsafe_author,
):
    row = {"id": unsafe_id, "title": unsafe_title}
    if unsafe_author is not None:
        row["author"] = unsafe_author
    with pytest.raises(ContractUnavailable):
        candidate_results({"items": [row]}, "cifericaopds.alpha")


def test_candidate_records_accept_stable_opaque_identifiers_and_isbn_fallback():
    row = candidate_results({"items": [{
        "id": "urn:uuid:550e8400-e29b-41d4-a716-446655440000",
        "title": "Un livre avec sous-titre", "author": "Auteur Exemple",
        "isbn13": "3127020510184",  # not Bookland
        "isbn": "9782211348171",    # valid Bookland fallback
    }]}, "cifericaopds.alpha")[0]
    assert row.record_id == "urn:uuid:550e8400-e29b-41d4-a716-446655440000"
    assert row.title == "Un livre avec sous-titre"
    assert row.author == "Auteur Exemple"
    assert row.metadata["isbn"] == "9782211348171"


def test_invalid_query_is_rejected_before_fixture_search_or_network(tmp_path):
    (tmp_path / "fixture-sources.json").write_text(json.dumps(inventory("alpha")))
    coll = CifericaCollection().activate(context(tmp_path))
    coll.refresh()
    session = coll.create_provider("alpha").create_session(
        credential_id="__public__", label="alpha", credentials={},
        options={}, config=None, storage=None, diagnostics=None,
    )
    for query in (None, 42, "search\ninjected", "a" * 301):
        with pytest.raises(ContractUnavailable):
            session.search(query)
    session.close()


def test_optional_scalar_with_control_char_does_not_reach_search_result():
    result = candidate_results({"items": [{
        "id": "book-7", "title": "Titre", "author": "Auteur",
        "publisher": "Éditions\nSecret", "type": "fiction\tpayload",
        "format": "epub\rprivate",
    }]}, "cifericaopds.alpha")[0]
    assert result.publisher is None
    assert result.type is None
    assert result.format is None


def test_existing_sessions_fail_closed_on_disable_remove_and_revision(tmp_path):
    fixture = tmp_path / "fixture-sources.json"
    fixture.write_text(json.dumps(inventory("alpha", "beta", generation=1)))
    (tmp_path / "fixture-results.json").write_text(json.dumps({
        "alpha": {"items": [{"id": "1", "title": "Livre Alpha"}]},
        "beta": {"items": [{"id": "2", "title": "Livre Beta"}]},
    }))
    coll = CifericaCollection().activate(context(tmp_path))
    coll.refresh()

    def session(source_id):
        result = coll.create_provider(source_id).create_session(
            credential_id="__public__", label=source_id, credentials={},
            options={}, config=None, storage=None, diagnostics=None,
        )
        result.connect()
        return result

    alpha = session("alpha")
    beta = session("beta")
    assert len(alpha.search("Livre")) == 1
    assert len(beta.search("Livre")) == 1

    # Disabling alpha does not revoke unrelated beta or erase saved inventory.
    disabled = inventory("alpha", "beta", generation=2)
    disabled["sources"][0]["status"] = "disabled"
    # Do not revise beta: old beta sessions are still allowed to search.
    disabled["sources"][1]["revision"] = "v1"
    disabled["sources"][1]["catalog_generation"] = 1
    fixture.write_text(json.dumps(disabled))
    coll.refresh()
    with pytest.raises(ContractUnavailable, match="inactive or changed"):
        alpha.search("Livre")
    with pytest.raises(ContractUnavailable):
        coll.create_provider("alpha")
    assert len(beta.search("Livre")) == 1

    # Reappearing alpha requires a NEW provider/session.
    fixture.write_text(json.dumps(inventory("alpha", "beta", generation=3)))
    coll.refresh()
    with pytest.raises(ContractUnavailable, match="inactive or changed"):
        alpha.search("Livre")
    current = session("alpha")
    assert len(current.search("Livre")) == 1

    # A new catalogue generation invalidates only prior sessions.
    fixture.write_text(json.dumps(inventory("alpha", "beta", generation=4)))
    coll.refresh()
    with pytest.raises(ContractUnavailable, match="inactive or changed"):
        current.search("Livre")
    assert len(session("alpha").search("Livre")) == 1

    retired = inventory("alpha", "beta", generation=5)
    retired["sources"][0]["status"] = "removed"
    fixture.write_text(json.dumps(retired))
    coll.refresh()
    with pytest.raises(ContractUnavailable, match="inactive or changed"):
        current.search("Livre")
    with pytest.raises(ContractUnavailable):
        coll.create_provider("alpha")
    assert (tmp_path / "inventory.json").exists()


def test_metadata_only_virtual_source_cannot_search(tmp_path):
    rows = inventory("alpha", generation=1)
    rows["sources"][0]["capabilities"] = ["metadata.enrich"]
    (tmp_path / "fixture-sources.json").write_text(json.dumps(rows))
    (tmp_path / "fixture-results.json").write_text(json.dumps({
        "alpha": {"items": [{"id": "1", "title": "Livre Alpha"}]},
    }))
    coll = CifericaCollection().activate(context(tmp_path))
    coll.refresh()
    provider = coll.create_provider("alpha")
    assert not provider.descriptor.capabilities.search
    session = provider.create_session(
        credential_id="__public__", label="alpha", credentials={},
        options={}, config=None, storage=None, diagnostics=None,
    )
    with pytest.raises(ContractUnavailable, match="inactive or changed"):
        session.search("Livre")


def test_removed_virtual_source_blocks_existing_session_http(tmp_path, mocked_candidate_server):
    url, calls, state = mocked_candidate_server
    coll = CifericaCollection().activate(context(
        tmp_path, mode="candidate_v02", base_url=url,
    ))
    coll.refresh()
    session = coll.create_provider("alpha").create_session(
        credential_id="__public__", label="alpha", credentials={},
        options={}, config=None, storage=None, diagnostics=None,
    )
    session.connect()
    assert len(session.search("Livre")) == 1
    state["sources"] = inventory("alpha", "beta", generation=2)
    state["sources"]["sources"][0]["status"] = "removed"
    coll.refresh()
    count = len(calls)
    with pytest.raises(ContractUnavailable, match="inactive or changed"):
        session.search("Livre")
    assert len(calls) == count  # No network request from stale session.
    with pytest.raises(ContractUnavailable):
        coll.create_provider("alpha")


def test_retired_source_reintroduced_without_revision_change_needs_new_session(tmp_path):
    fixture = tmp_path / "fixture-sources.json"
    original = inventory("alpha", generation=1)
    fixture.write_text(json.dumps(original))
    (tmp_path / "fixture-results.json").write_text(json.dumps({
        "alpha": {"items": [{"id": "1", "title": "Livre Alpha"}]},
    }))
    coll = CifericaCollection().activate(context(tmp_path))
    coll.refresh()
    def session():
        return coll.create_provider("alpha").create_session(
            credential_id="__public__", label="alpha", credentials={},
            options={}, config=None, storage=None, diagnostics=None,
        )
    stale = session()
    assert len(stale.search("Livre")) == 1

    retired = inventory("alpha", generation=2)
    retired["sources"][0].update({
        "status": "removed", "revision": "v1", "catalog_generation": 1,
    })
    fixture.write_text(json.dumps(retired))
    coll.refresh()
    with pytest.raises(ContractUnavailable):
        stale.search("Livre")

    returned = inventory("alpha", generation=3)
    returned["sources"][0].update({"revision": "v1", "catalog_generation": 1})
    fixture.write_text(json.dumps(returned))
    coll.refresh()
    with pytest.raises(ContractUnavailable):
        stale.search("Livre")
    assert len(session().search("Livre")) == 1


def test_refresh_during_http_search_discards_inflight_response(tmp_path):
    fixture = tmp_path / "fixture-sources.json"
    fixture.write_text(json.dumps(inventory("alpha", generation=1)))
    coll = CifericaCollection().activate(context(tmp_path))
    coll.refresh()
    session = coll.create_provider("alpha").create_session(
        credential_id="__public__", label="alpha", credentials={},
        options={}, config=None, storage=None, diagnostics=None,
    )
    calls = []

    def get_with_refresh(endpoint, *, params):
        calls.append((endpoint, params))
        updated = inventory("alpha", generation=2)
        updated["sources"][0]["status"] = "disabled"
        fixture.write_text(json.dumps(updated))
        coll.refresh()
        return {"items": [{"id": "first", "title": "A stale book"}]}

    # Exercise the candidate HTTP search boundary with a synchronous fake
    # client so the source invalidates *during* its upstream request.
    session._mode = "candidate_v02"
    session._client = NS(get=get_with_refresh)
    with pytest.raises(ContractUnavailable, match="changed during search"):
        session.search("book")
    assert len(calls) == 1
    with pytest.raises(ContractUnavailable, match="inactive or changed"):
        session.search("book")
    assert len(calls) == 1


def test_parent_deactivation_revokes_sessions_even_if_reactivated(tmp_path):
    fixture = tmp_path / "fixture-sources.json"
    fixture.write_text(json.dumps(inventory("alpha", generation=1)))
    (tmp_path / "fixture-results.json").write_text(json.dumps({
        "alpha": {"items": [{"id": "1", "title": "Livre Alpha"}]},
    }))
    ctx = context(tmp_path)
    coll = CifericaCollection().activate(ctx)
    coll.refresh()
    provider = coll.create_provider("alpha")
    old = provider.create_session(
        credential_id="__public__", label="alpha", credentials={},
        options={}, config=None, storage=None, diagnostics=None,
    )
    assert len(old.search("Livre")) == 1
    coll.deactivate()
    with pytest.raises(ContractUnavailable, match="inactive or changed"):
        old.search("Livre")
    with pytest.raises(ContractUnavailable):
        coll.create_provider("alpha")
    with pytest.raises(ContractUnavailable):
        coll.refresh()
    coll.activate(ctx)
    assert coll.snapshot().discovery_generation == 1
    with pytest.raises(ContractUnavailable, match="inactive or changed"):
        old.search("Livre")
    fresh = coll.create_provider("alpha").create_session(
        credential_id="__public__", label="alpha", credentials={},
        options={}, config=None, storage=None, diagnostics=None,
    )
    assert len(fresh.search("Livre")) == 1


def test_refresh_finishing_after_parent_deactivation_is_not_published(tmp_path):
    (tmp_path / "fixture-sources.json").write_text(json.dumps(inventory("alpha", generation=1)))
    coll = CifericaCollection().activate(context(tmp_path))
    coll.refresh()
    baseline = (tmp_path / "inventory.json").read_bytes()
    coll._mode = "candidate_v02"
    seen = []

    def stale_get(endpoint):
        seen.append(endpoint)
        coll.deactivate()
        return inventory("alpha", "beta", generation=2)

    coll._client = NS(get=stale_get)
    with pytest.raises(ContractUnavailable, match="changed during source refresh"):
        coll.refresh()
    assert seen == ["/api/v1/sources"]
    assert (tmp_path / "inventory.json").read_bytes() == baseline
    with pytest.raises(ContractUnavailable):
        coll.create_provider("beta")


@pytest.mark.parametrize("invalid_bytes", [
    b'{"api_major":',
    b'{"api_major": NaN}',
    b'\xff\xfe{"api_major": 1}',
    b' ' * (1024 * 1024 + 1),
])
def test_broken_source_fixture_does_not_replace_last_valid_inventory(
    tmp_path, invalid_bytes,
):
    sources = tmp_path / "fixture-sources.json"
    sources.write_text(json.dumps(inventory("alpha", generation=1)))
    coll = CifericaCollection().activate(context(tmp_path))
    expected = coll.refresh()
    baseline = (tmp_path / "inventory.json").read_bytes()

    sources.write_bytes(invalid_bytes)
    with pytest.raises(ContractUnavailable) as exc:
        coll.refresh()
    assert "fixture-sources" not in str(exc.value)
    assert str(tmp_path) not in str(exc.value)
    assert coll.snapshot() == expected
    assert (tmp_path / "inventory.json").read_bytes() == baseline


@pytest.mark.parametrize("invalid_bytes", [
    b'{"alpha": { "items":',
    b'{"alpha": {"items": [NaN]}}',
    b'\xff\xfe',
    b' ' * (1024 * 1024 + 1),
])
def test_broken_results_fixture_fails_closed_and_remains_local(
    tmp_path, invalid_bytes,
):
    (tmp_path / "fixture-sources.json").write_text(json.dumps(inventory("alpha")))
    coll = CifericaCollection().activate(context(tmp_path))
    coll.refresh()
    session = coll.create_provider("alpha").create_session(
        credential_id="__public__", label="alpha", credentials={},
        options={}, config=None, storage=None, diagnostics=None,
    )
    (tmp_path / "fixture-results.json").write_bytes(invalid_bytes)
    with pytest.raises(ContractUnavailable) as exc:
        session.search("livre")
    assert str(tmp_path) not in str(exc.value)
    assert coll.snapshot().children[0].source_id == "alpha"


def test_missing_source_fixture_is_typed_error_without_losing_cache(tmp_path):
    sources = tmp_path / "fixture-sources.json"
    sources.write_text(json.dumps(inventory("alpha", generation=1)))
    coll = CifericaCollection().activate(context(tmp_path))
    saved = coll.refresh()
    sources.unlink()
    with pytest.raises(ContractUnavailable, match="missing local source fixture"):
        coll.refresh()
    assert coll.snapshot() == saved
    assert (tmp_path / "inventory.json").exists()


def test_corrupted_cached_inventory_does_not_break_activation(tmp_path):
    ctx = context(tmp_path)
    cache = tmp_path / "inventory.json"
    for payload in (b'{', b'\xff', b' ' * (1024 * 1024 + 1), b'null'):
        cache.write_bytes(payload)
        coll = CifericaCollection().activate(ctx)
        with pytest.raises(ContractUnavailable, match="no validated inventory"):
            coll.snapshot()
        coll.deactivate()
    (tmp_path / "fixture-sources.json").write_text(json.dumps(inventory("alpha")))
    coll = CifericaCollection().activate(ctx)
    coll.refresh()
    assert coll.snapshot().children[0].source_id == "alpha"


def test_local_fixture_symlink_is_rejected_without_leaking_target(tmp_path):
    private = tmp_path / "private.json"
    private.write_text(json.dumps(inventory("alpha", generation=1)))
    sources = tmp_path / "fixture-sources.json"
    try:
        sources.symlink_to(private)
    except (OSError, NotImplementedError):
        pytest.skip("symlink is not supported on this test system")
    coll = CifericaCollection().activate(context(tmp_path))
    with pytest.raises(ContractUnavailable, match="unreadable local source JSON"):
        coll.refresh()
    assert private.read_text().startswith('{"api_major"')


def test_result_fixture_is_optional_but_invalid_directory_is_not(tmp_path):
    (tmp_path / "fixture-sources.json").write_text(json.dumps(inventory("alpha")))
    coll = CifericaCollection().activate(context(tmp_path))
    coll.refresh()
    session = coll.create_provider("alpha").create_session(
        credential_id="__public__", label="alpha", credentials={},
        options={}, config=None, storage=None, diagnostics=None,
    )
    assert session.search("livre") == []
    (tmp_path / "fixture-results.json").mkdir()
    with pytest.raises(ContractUnavailable):
        session.search("livre")


def test_inventory_publication_is_private_and_does_not_follow_stale_tmp(tmp_path):
    import os
    private = tmp_path / "private.txt"
    private.write_text("DO NOT OVERWRITE")
    # Legacy fixed-name temp file could have been redirected to a target.
    (tmp_path / "inventory.tmp").symlink_to(private)
    (tmp_path / "fixture-sources.json").write_text(json.dumps(inventory("alpha")))
    coll = CifericaCollection().activate(context(tmp_path))
    coll.refresh()
    assert private.read_text() == "DO NOT OVERWRITE"
    assert (tmp_path / "inventory.json").is_file()
    assert not list(tmp_path.glob(".cifericaopds-inventory-*.tmp"))
    if os.name == "posix":
        assert (tmp_path / "inventory.json").stat().st_mode & 0o777 == 0o600


def test_failed_inventory_publish_keeps_previous_snapshot_and_cleans_tmp(
    tmp_path, monkeypatch,
):
    import cifereca_cifericaopds as adapter
    sources = tmp_path / "fixture-sources.json"
    sources.write_text(json.dumps(inventory("alpha", generation=1)))
    coll = CifericaCollection().activate(context(tmp_path))
    original = coll.refresh()
    original_bytes = (tmp_path / "inventory.json").read_bytes()
    sources.write_text(json.dumps(inventory("alpha", "beta", generation=2)))

    def fail_replace(*_args, **_kwargs):
        raise PermissionError("private filesystem path")

    monkeypatch.setattr(adapter.os, "replace", fail_replace)
    with pytest.raises(ContractUnavailable, match="local inventory persistence unavailable") as exc:
        coll.refresh()
    assert "private filesystem path" not in str(exc.value)
    assert coll.snapshot() == original
    assert (tmp_path / "inventory.json").read_bytes() == original_bytes
    assert not list(tmp_path.glob(".cifericaopds-inventory-*.tmp"))



@pytest.mark.parametrize("body", [
    b'{"api_major":NaN,"sources":[]}',
    b'{"api_major":Infinity,"sources":[]}',
    b'{"api_major":1e999,"sources":[]}',
    b'{"api_major":1,"api_major":1,"sources":[]}',
    b'{"sources":[{"source_id":"alpha","source_id":"beta"}]}',
    b'\xff\xfe',
    b'{"incomplete":',
    b' ' * (1024 * 1024 + 1),
])
def test_candidate_http_strict_json_preserves_good_inventory(
    tmp_path, mocked_candidate_server, body,
):
    url, calls, state = mocked_candidate_server
    coll = CifericaCollection().activate(context(
        tmp_path, mode="candidate_v02", base_url=url,
    ))
    accepted = coll.refresh()
    baseline = (tmp_path / "inventory.json").read_bytes()
    state["raw"] = body
    with pytest.raises(ContractUnavailable) as exc:
        coll.refresh()
    assert exc.value.__cause__ is None
    assert coll.snapshot() == accepted
    assert (tmp_path / "inventory.json").read_bytes() == baseline
    assert len(calls) == 2


@pytest.mark.parametrize(("status", "content_type"), [
    (503, "application/json"),
    (401, "application/json"),
    (200, "text/html"),
    (200, "application/problem+json"),
])
def test_candidate_http_wrong_status_or_media_type_fails_closed(
    tmp_path, mocked_candidate_server, status, content_type,
):
    url, calls, state = mocked_candidate_server
    coll = CifericaCollection().activate(context(
        tmp_path, mode="candidate_v02", base_url=url,
    ))
    state["status"] = status
    state["content_type"] = content_type
    with pytest.raises(ContractUnavailable) as exc:
        coll.refresh()
    assert exc.value.__cause__ is None
    with pytest.raises(ContractUnavailable):
        coll.snapshot()
    assert len(calls) == 1


@pytest.mark.parametrize("timeout", [
    None, "invalid", "nan", float("inf"), float("-inf"), True, False,
])
def test_candidate_http_rejects_invalid_timeout_before_request(tmp_path, timeout):
    with pytest.raises(ContractUnavailable, match="invalid configured timeout") as exc:
        CifericaCollection().activate(context(
            tmp_path, mode="candidate_v02", timeout_seconds=timeout,
        ))
    assert exc.value.__cause__ is None


def test_local_inventory_rejects_duplicate_keys_and_nonfinite_float(tmp_path):
    ctx = context(tmp_path)
    fixture = tmp_path / "fixture-sources.json"
    fixture.write_text(json.dumps(inventory("alpha")))
    coll = CifericaCollection().activate(ctx)
    old = coll.refresh()
    old_bytes = (tmp_path / "inventory.json").read_bytes()
    for body in (
        b'{"api_major":1,"api_major":1,"sources":[]}',
        b'{"api_major":1,"discovery_generation":1e999,"sources":[]}',
    ):
        fixture.write_bytes(body)
        with pytest.raises(ContractUnavailable) as exc:
            coll.refresh()
        assert "local source JSON" in str(exc.value)
        assert coll.snapshot() == old
        assert (tmp_path / "inventory.json").read_bytes() == old_bytes



@pytest.mark.parametrize(("endpoint", "params"), [
    ("/api/v1/sources", {"q": "secret"}),
    ("/api/v1/search", None),
    ("/api/v1/search", {"source_id": "alpha"}),
    ("/api/v1/search", {"source_id": "alpha", "q": "Livre", "token": "secret"}),
    ("/api/v1/search", {"source_id": "alpha/beta", "q": "Livre"}),
    ("/api/v1/search", {"source_id": "alpha", "q": "token\nprivate"}),
    ("/api/v1/search", {"source_id": "alpha", "q": "a" * 301}),
    ("/api/v1/search/../secrets", None),
    ("/api/v1/private", None),
    ("/api/v1/sources?api_key=secret", None),
])
def test_candidate_http_restricts_endpoints_and_query_schema(
    mocked_candidate_server, endpoint, params,
):
    url, calls, _state = mocked_candidate_server
    client = JsonClient(url)
    with pytest.raises(ContractUnavailable):
        client.get(endpoint, params=params)
    assert calls == []


def test_candidate_http_bypasses_untrusted_environment_proxy(
    mocked_candidate_server, monkeypatch,
):
    url, calls, _state = mocked_candidate_server
    monkeypatch.setenv("HTTP_PROXY", "http://malicious.invalid:8080")
    monkeypatch.setenv("HTTPS_PROXY", "http://malicious.invalid:8080")
    monkeypatch.setenv("http_proxy", "http://malicious.invalid:8080")
    client = JsonClient(url)
    assert client.get("/api/v1/sources")["api_major"] == 1
    assert client.get("/api/v1/search", params={
        "source_id": "alpha", "q": "Un livre",
    })["items"][0]["title"] == "Livre HTTP"
    assert calls == [
        "/api/v1/sources",
        "/api/v1/search?source_id=alpha&q=Un+livre",
    ]



def test_synthetic_visibility_is_local_offline_and_survives_config_toggles(tmp_path):
    data = inventory("alpha", "merged", generation=1)
    data["sources"][1]["kind"] = "synthetic.merged"
    fixture = tmp_path / "fixture-sources.json"
    fixture.write_text(json.dumps(data))
    ctx_hidden = context(tmp_path, show_synthetic=False)
    first = CifericaCollection().activate(ctx_hidden)
    first.refresh()
    assert [entry.source_id for entry in first.snapshot().children] == ["alpha"]
    with pytest.raises(ContractUnavailable):
        first.create_provider("merged")

    saved = (tmp_path / "inventory.json").read_bytes()
    full = json.loads(saved)
    assert {entry["source_id"] for entry in full["children"]} == {"alpha", "merged"}
    first.deactivate()

    # No HTTP/refresh needed to restore previously accepted source metadata.
    ctx_shown = context(tmp_path, show_synthetic=True)
    shown = CifericaCollection().activate(ctx_shown)
    assert {entry.source_id for entry in shown.snapshot().children} == {"alpha", "merged"}
    assert shown.create_provider("merged").descriptor.id == "cifericaopds.merged"
    assert (tmp_path / "inventory.json").read_bytes() == saved

    # Same upstream discovery generation must be accepted after changing
    # a *local* visibility preference; this is not remote source drift.
    assert len(shown.refresh().children) == 2
    assert (tmp_path / "inventory.json").read_bytes() == saved
    shown.deactivate()

    hidden_again = CifericaCollection().activate(ctx_hidden)
    assert [entry.source_id for entry in hidden_again.snapshot().children] == ["alpha"]
    with pytest.raises(ContractUnavailable):
        hidden_again.create_provider("merged")
    assert len(hidden_again.refresh().children) == 1
    assert (tmp_path / "inventory.json").read_bytes() == saved


def test_same_generation_remote_change_still_refused_with_hidden_synthetic(tmp_path):
    data = inventory("alpha", "merged", generation=3)
    data["sources"][1]["kind"] = "synthetic.merged"
    fixture = tmp_path / "fixture-sources.json"
    fixture.write_text(json.dumps(data))
    coll = CifericaCollection().activate(context(tmp_path, show_synthetic=False))
    accepted = coll.refresh()
    saved = (tmp_path / "inventory.json").read_bytes()

    # Even changes to hidden sources must obey the Core generation contract.
    data["sources"][1]["revision"] = "changed-without-generation"
    fixture.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="same generation"):
        coll.refresh()
    assert coll.snapshot() == accepted
    assert (tmp_path / "inventory.json").read_bytes() == saved


def test_synthetic_source_never_visible_when_disabled_on_offline_restart(tmp_path):
    data = inventory("alpha", "merged", generation=1)
    data["sources"][1]["kind"] = "synthetic.merged"
    (tmp_path / "fixture-sources.json").write_text(json.dumps(data))
    original = CifericaCollection().activate(context(tmp_path, show_synthetic=True))
    original.refresh()
    original.deactivate()
    restart = CifericaCollection().activate(context(tmp_path, show_synthetic=False))
    assert {x.plugin_id for x in restart.snapshot().children} == {"cifericaopds.alpha"}
    with pytest.raises(ContractUnavailable):
        restart.create_provider("merged")



def test_snapshot_unavailable_after_deactivation_despite_prior_cache(tmp_path):
    (tmp_path / "fixture-sources.json").write_text(json.dumps(inventory("alpha")))
    collection = CifericaCollection().activate(context(tmp_path))
    collection.refresh()
    assert collection.snapshot().children[0].source_id == "alpha"
    callback = []
    collection.subscribe(lambda: callback.append("stale"))
    collection.deactivate()
    with pytest.raises(ContractUnavailable, match="no validated inventory"):
        collection.snapshot()
    with pytest.raises(ContractUnavailable):
        collection.create_provider("alpha")
    # The cache persists for a fresh explicit activation, but the retired
    # service may neither expose it nor retain its old subscriber.
    collection.activate(context(tmp_path))
    assert collection.snapshot().children[0].source_id == "alpha"
    updated = inventory("beta", generation=2)
    (tmp_path / "fixture-sources.json").write_text(json.dumps(updated))
    collection.refresh()
    assert callback == []


def test_slow_first_activation_cannot_overwrite_newer_activation(tmp_path, monkeypatch):
    import cifereca_cifericaopds as module
    from dataclasses import asdict
    from threading import Event, Thread

    first_dir, second_dir = tmp_path / "first", tmp_path / "second"
    first_context = context(first_dir, show_synthetic=True)
    second_context = context(second_dir, show_synthetic=False)
    (first_dir / "inventory.json").write_text(json.dumps(asdict(
        candidate_inventory(inventory("first"), None),
    )))
    (second_dir / "inventory.json").write_text(json.dumps(asdict(
        candidate_inventory(inventory("second"), None),
    )))
    original_read = module._read_local_json
    entered, release = Event(), Event()

    def blocked_read(path, *, label, optional=False):
        if path.parent == first_dir and label == "inventory":
            entered.set()
            if not release.wait(5):
                raise AssertionError("test activation was not released")
        return original_read(path, label=label, optional=optional)

    monkeypatch.setattr(module, "_read_local_json", blocked_read)
    collection = CifericaCollection()
    errors = []

    def first_activate():
        try:
            collection.activate(first_context)
        except BaseException as exc:
            errors.append(exc)

    thread = Thread(target=first_activate, daemon=True)
    thread.start()
    try:
        assert entered.wait(5)
        collection.activate(second_context)
        assert [x.source_id for x in collection.snapshot().children] == ["second"]
    finally:
        release.set()
        thread.join(timeout=5)
    assert not thread.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], ContractUnavailable)
    assert str(errors[0]) == "plugin changed during activation"
    assert [x.source_id for x in collection.snapshot().children] == ["second"]
    assert collection._directory == second_dir


def test_deactivate_during_activation_does_not_resurrect_plugin(tmp_path, monkeypatch):
    import cifereca_cifericaopds as module
    from threading import Event, Thread

    ctx = context(tmp_path)
    entered, release = Event(), Event()
    original_read = module._read_local_json

    def blocked_read(path, *, label, optional=False):
        entered.set()
        if not release.wait(5):
            raise AssertionError("test activation was not released")
        return original_read(path, label=label, optional=optional)

    monkeypatch.setattr(module, "_read_local_json", blocked_read)
    collection = CifericaCollection()
    errors = []

    def activate():
        try:
            collection.activate(ctx)
        except BaseException as exc:
            errors.append(exc)

    thread = Thread(target=activate, daemon=True)
    thread.start()
    try:
        assert entered.wait(5)
        collection.deactivate()
        with pytest.raises(ContractUnavailable):
            collection.snapshot()
    finally:
        release.set()
        thread.join(timeout=5)
    assert not thread.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], ContractUnavailable)
    assert collection._active is False
    assert collection._directory is None
    with pytest.raises(ContractUnavailable):
        collection.refresh()


def test_failed_reactivation_does_not_publish_previous_snapshot(tmp_path):
    (tmp_path / "fixture-sources.json").write_text(json.dumps(inventory("alpha")))
    collection = CifericaCollection().activate(context(tmp_path))
    collection.refresh()
    with pytest.raises(ContractUnavailable, match="unknown plugin mode"):
        collection.activate(context(tmp_path, mode="unsupported"))
    with pytest.raises(ContractUnavailable):
        collection.snapshot()
    with pytest.raises(ContractUnavailable):
        collection.create_provider("alpha")
    # User may still recover by activating the same previously accepted cache.
    collection.activate(context(tmp_path))
    assert [x.source_id for x in collection.snapshot().children] == ["alpha"]


@pytest.mark.parametrize("private", [
    "Livre token=PRIVATE_CREDENTIAL",
    "Livre cookie=PRIVATE_SESSION",
    "Livre secret: PRIVATE_KEY",
    "Livre refresh_token=PRIVATE_REFRESH",
    "Livre Authorization=Bearer PRIVATE_HEADER",
    "Livre Bearer PRIVATE_BEARER",
    "Livre /home/private/borrower.epub",
    "Livre C:\\Users\\Private\\loan.epub",
    "Livre file:///home/private/loan.epub",
    "Livre\u202ePRIVATE_BIDI",
    "Livre\u2066PRIVATE_ISOLATE\u2069",
    "Livre\u200ePRIVATE_MARK",
    "Livre\u0091PRIVATE_C1",
    "Livre\ud800",
])
def test_candidate_rejects_private_or_spoofed_mandatory_bibliography(private):
    """Untrusted publisher data must not reach cache/index/GTK through titles."""
    with pytest.raises(ContractUnavailable, match="invalid candidate title"):
        candidate_results({"items": [{"id": "safe", "title": private}]},
                          "cifericaopds.alpha")
    with pytest.raises(ContractUnavailable, match="invalid candidate author"):
        candidate_results({"items": [
            {"id": "safe", "title": "Titre public", "author": private},
        ]}, "cifericaopds.alpha")


@pytest.mark.parametrize("private", [
    "token=SECRET", "cookie=PRIVATE", "Bearer PRIVATE",
    "secret:PRIVATE", "https://signed.example/?token=VALUE",
    "Texte\u202eavec override", "Texte\u2066avec isolate\u2069",
    "Valeur\ud800",
])
def test_candidate_discards_private_optional_metadata_without_losing_public_notice(private):
    rows = candidate_results({"items": [{
        "id": "book-1", "title": "Les secrets des jardins",
        "author": "Anaïs Martin", "publisher": private,
        "type": private, "format": private,
        "isbn13": "9782211348171",
    }]}, "cifericaopds.alpha")
    assert len(rows) == 1
    row = rows[0]
    assert row.publisher is None and row.type is None and row.format is None
    assert row.metadata == {"source_id": "alpha", "isbn": "9782211348171"}
    assert row.title == "Les secrets des jardins"
    assert row.author == "Anaïs Martin"


def test_non_ascii_bookland_digits_never_become_canonical_isbn():
    # Arabic-Indic "١" isdecimal()/isdigit() but not a canonical ISBN digit.
    row = candidate_results({"items": [{
        "id": "book-2", "title": "Lecture publique",
        "isbn13": "978221134817١", "isbn": "9782211348171",
    }]}, "cifericaopds.alpha")[0]
    assert row.metadata["isbn"] == "9782211348171"
    without_fallback = candidate_results({"items": [{
        "id": "book-3", "title": "Lecture publique",
        "isbn13": "978221134817١",
    }]}, "cifericaopds.alpha")[0]
    assert "isbn" not in without_fallback.metadata


def test_legitimate_accented_bibliography_remains_available():
    row = candidate_results({"items": [{
        "id": "book-4", "title": "À la recherche du secret",
        "author": "Anaïs Sautier et Łukasz Żak",
        "publisher": "Éditions L'École des loisirs",
        "type": "roman", "format": "papier",
    }]}, "cifericaopds.alpha")[0]
    assert row.title == "À la recherche du secret"
    assert "Łukasz Żak" in row.author
    assert row.publisher == "Éditions L'École des loisirs"


def test_candidate_fixture_search_fields_are_exact_and_public_only(tmp_path):
    """#969: an ISBN filter cannot silently degrade into a title search."""
    ctx = context(tmp_path)
    (tmp_path / "fixture-sources.json").write_text(json.dumps(inventory("alpha")))
    (tmp_path / "fixture-results.json").write_text(json.dumps({
        "alpha": {"items": [
            {"id": "title-only", "title": "9780306406157 catalogue"},
            {"id": "real-edition", "title": "La vraie édition",
             "author": "Auteure Test", "isbn13": "9780306406157",
             "language": "fr"},
            {"id": "other", "title": "Autre ouvrage",
             "author": "Autrice Différente", "isbn13": "9780140328721",
             "language": "en"},
        ]},
    }))
    collection = CifericaCollection().activate(ctx)
    collection.refresh(explicit=True)
    plugin = collection.create_provider("alpha")
    session = plugin.create_session(
        credential_id="__public__", label="Alpha",
        credentials={}, options={}, config=None, storage=None,
        diagnostics=None,
    )
    session.connect()
    try:
        assert [r.record_id for r in session.search(
            "9780306406157", field="isbn",
        )] == ["real-edition"]
        assert [r.record_id for r in session.search(
            "978-0-306-40615-7", field="isbn",
        )] == ["real-edition"]
        assert session.search("9780306406158", field="isbn") == []
        assert [r.record_id for r in session.search(
            "9780306406157", field="title",
        )] == ["title-only"]
        assert [r.record_id for r in session.search(
            "test", field="author",
        )] == ["real-edition"]
        assert [r.record_id for r in session.search(
            "FR", field="language",
        )] == ["real-edition"]
        assert session.search("fr", field="title") == []
        for unsupported in ("subject", "series", "isbn_or_title", "", None):
            with pytest.raises(ContractUnavailable, match="unsupported candidate search field"):
                session.search("ouvrage", field=unsupported)
    finally:
        session.close()


def test_candidate_remote_search_rejects_unverified_field_before_any_http(tmp_path):
    (tmp_path / "fixture-sources.json").write_text(json.dumps(inventory("alpha")))
    collection = CifericaCollection().activate(context(tmp_path))
    collection.refresh(explicit=True)
    plugin = collection.create_provider("alpha")
    session = plugin.create_session(
        credential_id="__public__", label="Alpha",
        credentials={}, options={}, config=None, storage=None,
        diagnostics=None,
    )
    session.connect()
    calls = []
    session._mode = "candidate_v02"
    session._client = NS(get=lambda *a, **kw: calls.append((a, kw)))
    try:
        for field in ("isbn", "title", "author", "language"):
            with pytest.raises(ContractUnavailable, match="search field unverified"):
                session.search("9780306406157", field=field)
        assert calls == []
    finally:
        session.close()


def test_candidate_http_is_captured_by_core_only_in_trace(mocked_candidate_server,monkeypatch):
    from cifereca.http_trace import HTTPTraceCapture
    import cifereca.http_trace as trace_module
    from cifereca.logging_utils import diagnostics_level,set_diagnostics_level
    import cifereca_cifericaopds as module
    url,calls,_state=mocked_candidate_server
    capture=HTTPTraceCapture()
    monkeypatch.setattr(trace_module,'HTTP_TRACE',capture)
    original=module.HTTPX_TRANSPORT.client
    constructors=[]

    def construct(**options):
        constructors.append(options.copy())
        return original(**options)

    monkeypatch.setattr(module.HTTPX_TRANSPORT,'client',construct)
    previous=diagnostics_level()
    client=JsonClient(url)
    assert not constructors and not calls  # construction does not do I/O
    try:
        for level in ('normal','trace','normal'):
            set_diagnostics_level(level)
            assert client.get('/api/v1/sources')['api_major']==1
        assert calls==['/api/v1/sources']*3
        assert len(constructors)==3
        assert all(o['producer']=='cifericaopds' and o['trust_env'] is False
                   and o['follow_redirects'] is False for o in constructors)
        entries=capture.snapshot()['exchanges']
        assert len(entries)==1
        body=entries[0]['response_body']
        assert body['state']=='complete'
        assert json.loads(body['data'])['api_major']==1
    finally:
        set_diagnostics_level(previous)
