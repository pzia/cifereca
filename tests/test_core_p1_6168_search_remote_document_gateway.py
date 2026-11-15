"""#6168: remote Search list/detail can share exact N1/N2 without URL joins."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.gui.cover_loader import CoverLoader
from cifereca.gui.document_cover_service import DocumentCoverService
from cifereca.gui.search_cover_bridge import SearchCoverBridge
from cifereca.gui.search_document_cover_bridge import (
    SearchDocumentCoverRegistry, exact_remote_search_document,
    register_exact_search_document, search_document_is_current,
)
from tests.test_core_p1_3104_search_detail_cover_live_row import (
    item, detail, COVER,
)


CATALOG = "https://catalog.example.org/edition-cover.jpg"
CONTEXT = (3, 4, 5, 6)


def host(source=None, *, context=CONTEXT):
    source = source or replace(item(), thumbnail_url=CATALOG)
    key = "provider:display-1"
    window = NS(
        _closed=False, _search_mode="global", _online_cover_context=context,
        _loaded_search_context=("q", "title", "all", "alice"),
        search_controls=NS(
            state=lambda: NS(text="q", field="title", scope="all"),
        ),
        _identity_context_generation=context[0],
        _search_generation=context[1],
        service=NS(
            current_identity=NS(identity_id="alice"),
            _account_context_epoch=context[2],
            _source_context_epoch=context[3],
        ),
        search_objects={key: source},
        _search_cover_bridge=SearchCoverBridge(),
        search_document_cover_registry=SearchDocumentCoverRegistry(),
    )
    return window, source, key


@pytest.fixture
def gateway(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    loader = CoverLoader(max_workers=2)
    calls = []
    # No network: N2 selection is tested independently from physical N3.
    monkeypatch.setattr(
        loader, "load",
        lambda url, callback, **kwargs: (
            calls.append(url) or NS(cancel=lambda: None)
        ),
    )
    service = DocumentCoverService(loader)
    try:
        yield service, calls
    finally:
        service.close()
        loader.close()


def test_remote_search_owns_only_its_catalogue_image(gateway):
    window, source, key = host()
    service, calls = gateway
    proof = exact_remote_search_document(window, source, key)
    assert proof is not None
    assert proof.document.identity_id == "alice"
    assert [c.tier for c in proof.candidates] == ["catalog"]
    assert proof.candidates[0].url == CATALOG
    assert register_exact_search_document(
        window, source, service, proof, proof.document,
    )
    request = service.get_cover(proof.document, "detail", lambda path: None)
    assert request.source_uri == CATALOG
    assert calls == [CATALOG]
    assert service.decisions.peek(
        proof.document, revision=1,
    ).tier == "catalog"


def test_existing_exact_holdings_origin_outranks_hot_catalogue(gateway):
    window, source, key = host()
    service, calls = gateway
    assert window._search_cover_bridge.publish(
        source, detail(source), identity_id="alice", context=CONTEXT,
    )
    proof = exact_remote_search_document(window, source, key)
    assert proof is not None
    assert [c.tier for c in proof.candidates] == ["origin", "catalog"]
    assert [c.url for c in proof.candidates] == [COVER, CATALOG]
    assert register_exact_search_document(
        window, source, service, proof, proof.document,
    )
    request = service.get_cover(proof.document, "detail", lambda path: None)
    assert request.source_uri == COVER
    assert calls == [COVER]
    assert service.decisions.peek(
        proof.document, revision=1,
    ).tier == "origin"


@pytest.mark.parametrize("alter", [
    "identity", "query_epoch", "account_epoch", "source_epoch",
    "search_mode", "source_object", "source_key", "stale", "query_state",
])
def test_registration_refuses_expired_or_foreign_source(gateway, alter):
    window, source, key = host()
    service, _ = gateway
    proof = exact_remote_search_document(window, source, key)
    assert proof is not None
    if alter == "identity":
        window.service.current_identity.identity_id = "bob"
    elif alter == "query_epoch":
        window._search_generation += 1
    elif alter == "account_epoch":
        window.service._account_context_epoch += 1
    elif alter == "source_epoch":
        window.service._source_context_epoch += 1
    elif alter == "search_mode":
        window._search_mode = "local"
    elif alter == "source_object":
        window.search_objects[key] = replace(source)
    elif alter == "source_key":
        window.search_objects = {"other": source}
    elif alter == "query_state":
        window.search_controls.state = lambda: NS(
            text="other query", field="title", scope="all",
        )
    else:
        source.metadata["search_provenance"] = "known_catalog_stale"
    assert not search_document_is_current(window, proof)
    assert exact_remote_search_document(window, source, key) is None
    assert not register_exact_search_document(
        window, source, service, proof, proof.document,
    )
    assert service.decisions.peek(proof.document, revision=1) is None


def test_source_revision_aba_keeps_monotone_n2_tokens(gateway):
    window, source, key = host()
    service, _ = gateway
    first = exact_remote_search_document(window, source, key)
    assert first is not None
    assert register_exact_search_document(
        window, source, service, first, first.document,
    )
    one = service.get_cover(first.document, "detail", lambda path: None)
    changed = replace(source, thumbnail_url="https://catalog.example.org/other.jpg")
    window.search_objects[key] = changed
    second = exact_remote_search_document(window, changed, key)
    assert second is not None
    assert register_exact_search_document(
        window, changed, service, second, second.document,
    )
    assert not one.still_current
    again = replace(source)
    window.search_objects[key] = again
    third = exact_remote_search_document(window, again, key)
    assert third is not None
    assert register_exact_search_document(
        window, again, service, third, third.document,
    )
    assert service.decisions.peek(first.document, revision=1) is None
    assert service.decisions.peek(first.document, revision=2) is None
    final = service.get_cover(third.document, "detail", lambda path: None)
    assert final.source_uri == CATALOG
    assert service.decisions.peek(first.document, revision=3).uri == CATALOG


def test_source_revision_never_compares_untrusted_metadata_objects(gateway):
    class ExplodingValue:
        def __eq__(self, _other):
            raise AssertionError("provider-owned metadata equality forbidden")

    window, source, key = host()
    service, _ = gateway
    source.metadata["opaque"] = ExplodingValue()
    first = exact_remote_search_document(window, source, key)
    assert first is not None
    assert register_exact_search_document(
        window, source, service, first, first.document,
    )
    old = service.get_cover(first.document, "detail", lambda _value: None)
    newer = replace(source, metadata={"opaque": ExplodingValue()})
    window.search_objects[key] = newer
    second = exact_remote_search_document(window, newer, key)
    assert second is not None
    assert register_exact_search_document(
        window, newer, service, second, second.document,
    )
    assert not old.still_current


def test_replaced_source_can_never_borrow_registered_provenance(gateway):
    window, source, key = host()
    service, _ = gateway
    proof = exact_remote_search_document(window, source, key)
    other = replace(source, source_credential="different", thumbnail_url=CATALOG)
    window.search_objects[key] = other
    assert not register_exact_search_document(
        window, source, service, proof, proof.document,
    )


def test_no_out_of_context_source_without_current_online_lease():
    window, source, key = host()
    window._loaded_search_context = None
    assert exact_remote_search_document(window, source, key) is None
    window._loaded_search_context = object()
    window._closed = True
    assert exact_remote_search_document(window, source, key) is None


def test_registry_close_before_n2_prevents_future_publications(gateway):
    window, source, key = host()
    service, _ = gateway
    proof = exact_remote_search_document(window, source, key)
    assert proof is not None
    window.search_document_cover_registry.close()
    assert not register_exact_search_document(
        window, source, service, proof, proof.document,
    )


def test_only_visible_or_selected_gtk_binds_register_without_catalogue_scan():
    source = (
        Path(__file__).resolve().parents[1]
        / "src/cifereca/gui/search_document_cover_bridge.py"
    ).read_text(encoding="utf-8")
    assert "service.register(snapshot)" in source
    assert "local_book_path(" not in source
    assert "artifact_get(" not in source
    assert "catalog_entries(" not in source
    assert "get_cover(" not in source


def test_same_url_origin_promotion_rebinds_selected_row_before_detail(gateway):
    from tests.test_core_p1_3104_search_detail_cover_live_row import Row, publisher
    from cifereca.gui.search_document_cover_bridge import (
        attach_search_document, selected_search_document,
    )

    window, source, key = host()
    service, _ = gateway
    initial = exact_remote_search_document(window, source, key)
    row = Row(key)
    row.thumbnail_url = CATALOG
    row._public_row_content = ("search", key, initial.row_signature, CATALOG)
    attach_search_document(row, initial)
    window.search_page = NS(selection=NS(get_selected_item=lambda: row))
    assert register_exact_search_document(window, source, service, initial, initial.document)
    old = service.get_cover(initial.document, "list", lambda path: None)

    def rebind(signal):
        assert signal == "thumbnail-changed"
        proof = row._cover_search_evidence
        assert [candidate.tier for candidate in proof.candidates] == ["origin", "catalog"]
        assert register_exact_search_document(window, source, service, proof, proof.document)

    row.set_thumbnail_url = lambda url: False
    row.emit = rebind
    publisher()(window, source, detail(source, url=CATALOG))

    proof = selected_search_document(window, source)
    assert proof.document == initial.document
    assert row._public_row_content[-2] == proof.row_signature
    assert not old.still_current
    assert register_exact_search_document(window, source, service, proof, proof.document)
    current = service.get_cover(proof.document, "detail", lambda path: None)
    assert current.source_uri == CATALOG
    assert service.decisions.peek(proof.document, revision=2).tier == "origin"


def anonymous_catalogue(**changes):
    public = replace(
        item(), source_credential="__public__", source_collection="public_catalog",
        thumbnail_url=CATALOG,
    )
    return replace(public, **changes)


def activate_public_search(window, source, key, identity, context):
    window.service.current_identity.identity_id = identity
    window._identity_context_generation, window._search_generation = context[:2]
    window.service._account_context_epoch, window.service._source_context_epoch = context[2:]
    window._online_cover_context = context
    window._loaded_search_context = ("q", "title", "all", identity)
    window.search_objects = {key: source}


def test_identical_public_cover_reuses_one_decision_and_one_get_across_profiles(tmp_path, monkeypatch):
    from io import BytesIO
    from threading import Event

    import httpx
    from PIL import Image

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    source = anonymous_catalogue()
    window, _, key = host(source)
    loader = CoverLoader(max_workers=2)
    service = DocumentCoverService(loader)
    response = BytesIO()
    Image.new("RGB", (12, 18), "red").save(response, format="PNG")
    calls = []

    def serve(request):
        calls.append((request.method, str(request.url)))
        return httpx.Response(
            200, headers={"content-type": "image/png"}, content=response.getvalue(),
        )

    loader.client.close()
    loader.client = httpx.Client(transport=httpx.MockTransport(serve), trust_env=False)
    try:
        proof = exact_remote_search_document(window, source, key)
        assert proof is not None and proof.document.kind == "public_catalogue"
        assert proof.document.identity_id is None
        assert register_exact_search_document(window, source, service, proof, proof.document)
        paths, received = [], Event()
        first = service.get_cover(
            proof.document, "detail", lambda path: (paths.append(path), received.set()),
        )
        assert received.wait(5) and paths[0] is not None
        token = service.decisions.selected_token(proof.document, revision=1, uri=CATALOG)
        assert token is not None

        bob = anonymous_catalogue()
        activate_public_search(window, bob, key, "bob", (4, 5, 6, 7))
        assert not search_document_is_current(window, proof)
        second = exact_remote_search_document(window, bob, key)
        assert second.document == proof.document
        assert register_exact_search_document(window, bob, service, second, second.document)
        assert service.decisions.selected_token(second.document, revision=1, uri=CATALOG) == token
        service.get_cover(second.document, "detail", paths.append)
        assert paths == [paths[0], paths[0]] and first.still_current
        assert calls == [("GET", CATALOG)]

        activate_public_search(window, source, key, "alice", (5, 6, 7, 8))
        assert not search_document_is_current(window, proof)
        assert not search_document_is_current(window, second)
        assert not register_exact_search_document(window, source, service, proof, proof.document)
    finally:
        service.close()
        loader.close()


@pytest.mark.parametrize("change", [
    {"source_credential": "member-card"},
    {"source_collection": "library"},
    {"source_account_id": ""},
    {"owned": True},
    {"downloadable": True},
    {"download_url": "https://catalog.example.org/file.epub"},
])
def test_account_or_offer_coordinates_cannot_publish_public_document(change):
    window, source, key = host(anonymous_catalogue(**change))
    proof = exact_remote_search_document(window, source, key)
    assert proof is not None and proof.document.kind == "catalogue"
    assert proof.document.identity_id == "alice"


def test_public_listing_echo_reuses_key_but_distinct_holdings_is_scoped(gateway):
    source = anonymous_catalogue()
    window, _, key = host(source)
    service, _ = gateway
    shared = exact_remote_search_document(window, source, key)
    assert register_exact_search_document(window, source, service, shared, shared.document)
    service.get_cover(shared.document, "detail", lambda path: None)

    bridge = window._search_cover_bridge
    assert bridge.publish(source, detail(source, url=CATALOG), identity_id="alice", context=CONTEXT)
    assert exact_remote_search_document(window, source, key) == shared

    assert bridge.publish(source, detail(source), identity_id="alice", context=CONTEXT)
    private = exact_remote_search_document(window, source, key)
    assert private.document.identity_id == "alice"
    assert private.candidates[0].url == COVER
    assert not search_document_is_current(window, shared)

    bob = anonymous_catalogue()
    activate_public_search(window, bob, key, "bob", (4, 5, 6, 7))
    current = exact_remote_search_document(window, bob, key)
    assert current.document == shared.document
    assert [candidate.url for candidate in current.candidates] == [CATALOG]


def test_public_revision_and_exact_edition_are_independent_from_shared_image(gateway):
    service, _ = gateway
    window, source, key = host(anonymous_catalogue())
    first = exact_remote_search_document(window, source, key)
    assert register_exact_search_document(window, source, service, first, first.document)
    initial = service.get_cover(first.document, "detail", lambda path: None)

    for other in (anonymous_catalogue(record_id="edition-b"), anonymous_catalogue(revision_id="v2")):
        window.search_objects[key] = other
        proof = exact_remote_search_document(window, other, key)
        assert proof.document != first.document
        assert proof.candidates[0].url == CATALOG
        assert register_exact_search_document(window, other, service, proof, proof.document)

    new_url = "https://catalog.example.org/cover-updated.jpg"
    changed = anonymous_catalogue(thumbnail_url=new_url)
    window.search_objects[key] = changed
    proof = exact_remote_search_document(window, changed, key)
    assert proof.document == first.document
    assert register_exact_search_document(window, changed, service, proof, proof.document)
    assert not initial.still_current
    assert service.get_cover(proof.document, "detail", lambda path: None).source_uri == new_url
