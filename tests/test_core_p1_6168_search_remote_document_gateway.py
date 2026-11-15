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
    register_exact_search_document,
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
        _closed=False, _search_mode="global",
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
