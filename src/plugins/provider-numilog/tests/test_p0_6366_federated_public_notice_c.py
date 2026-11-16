"""P0 #6366: real anonymous Numilog response enters *federated* Core C/N2.

The supplier has no SQL account owner. Core requires a same-call
PublicSearchNotice rather than accepting a legacy list-only private search.
Only a truly anonymous session may retain a verified catalogue thumbnail;
a foreign private session's generic public projection still strips URLs.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS

from cifereca.config import Config
from cifereca.diagnostics import Diagnostics
from cifereca.services import LibraryService
from cifereca.storage import Storage
from cifereca.core.notice_cover_repository import read_notice_cover
from cifereca.core.search_orchestration import _public_rows_from_response
from cifereca.plugin_api import ProviderSearchResponse, PublicSearchNotice
import cifereca_numilog_public_provider as numilog
from cifereca_numilog_public import parse_public_search


SOURCE = (
    "https://www.numilog.com/Pages/Recherche/ResultatRecherche.aspx"
    "?mode=brSimple&query=loup"
)
FIXTURE = Path(__file__).parent / "fixtures" / "observed_search-loup-0.html"


def build(tmp_path, monkeypatch):
    config = Config(tmp_path / "config.json")
    config.set("library.directory", str(tmp_path / "library"))
    config.set("identities", [
        {"id": "alice", "label": "Alice"},
        {"id": "bob", "label": "Bob"},
    ])
    config.set("providers.accounts", [])
    service = LibraryService(
        config, Storage(tmp_path / "notices.sqlite"), Diagnostics(),
    )
    parsed = parse_public_search(FIXTURE.read_text(encoding="utf-8"), SOURCE)
    assert parsed.items and parsed.items[0].thumbnail_url is not None
    calls = []
    monkeypatch.setattr(
        numilog, "fetch_public_search",
        lambda *a, **k: calls.append(1) or parsed,
    )
    session = numilog.NumilogPublicSession(
        credential_id="__public__", label="Anonymous public catalogue",
        credentials={}, options={},
    )
    session.connect()
    service._sessions = {session.provider_id: session}
    return service, session, calls


def test_actual_federated_sync_and_progressive_numilog_c_notice(
    tmp_path, monkeypatch,
):
    service, session, supplier_gets = build(tmp_path, monkeypatch)
    try:
        assert service.current_identity.identity_id == "alice"
        rows = service.search(
            "loup", provider_id=session.provider_id, force=True,
        )
        assert rows
        a = rows[0]
        assert a.source_provider == session.provider_id
        assert a.source_credential == "__public__"
        assert a.source_collection == "public_catalog"
        assert a.metadata.get("catalogue_access") == "shared_read_only"
        assert a.thumbnail_url == "https://couverture.numilog.com/9782702194287_GRANDE.jpg"
        assert a.owned is False and a.downloadable is False
        assert a.download_url is None and a.source_account_id is None
        assert a.price is None and a.currency is None
        assert len(supplier_gets) == 1
        # This producer is genuinely selected by LibraryService, not
        # invoked directly by a manually synthesized Core mirror.
        doc = a._core_notice_cover_document_ref
        persisted = read_notice_cover(service.storage, doc)
        assert doc.identity_id is None and persisted is not None
        assert persisted.attribute.candidates[0].url == a.thumbnail_url
        assert persisted.epoch == 1

        service.select_identity("bob")
        assert service.current_identity.identity_id == "bob"
        batches = []
        summary = service.search_progressive(
            "loup", provider_id=session.provider_id, force=True,
            on_batch=batches.append,
        )
        assert not summary.cancelled and summary.result_count >= 1
        assert batches and batches[0].results
        b = batches[0].results[0]
        assert b.thumbnail_url == a.thumbnail_url
        assert b.metadata.get("catalogue_access") == "shared_read_only"
        assert service.last_search[0]._core_notice_cover_document_ref == doc
        assert read_notice_cover(service.storage, doc).epoch == 1
        # This is one anonymous real session/cache; a changed viewer does
        # not re-fetch or parse the same HTTP page.
        assert len(supplier_gets) == 1
        service.select_identity("alice")
        replay = service.search(
            "loup", provider_id=session.provider_id, force=False,
        )
        assert replay and replay[0].thumbnail_url == a.thumbnail_url
        assert replay[0]._core_notice_cover_document_ref == doc
        assert len(supplier_gets) == 1
    finally:
        service.close()
        session.close()


def test_public_same_call_envelope_does_not_launder_private_thumbnail():
    from cifereca.models import SearchResult
    raw = SearchResult(
        id="public:one", record_id="one", docbase="NUMILOG",
        medium="digital", title="Public", author="Author", type="Book",
        format="EPUB", publisher=None, published_at=None, description=None,
        thumbnail_url="https://covers.example.org/ordinary.jpg",
        friendly_url=None, scenario="NUMILOG_PUBLIC",
        source_provider="numilog-catalog",
        source_credential="__public__", source_collection="public_catalog",
        owned=False, downloadable=False, download_url=None,
        revision_id="source-v2",
        metadata={"language": "fr", "private": "must be excluded"},
    )
    response = ProviderSearchResponse(
        results=[raw], public_notices=(PublicSearchNotice(raw),),
    )
    anonymous = NS(
        provider_id="numilog-catalog", credential_id="__public__",
        credentials={}, _accounts=[],
    )
    admitted = _public_rows_from_response(response, anonymous)
    assert len(admitted) == 1
    assert admitted[0].thumbnail_url == raw.thumbnail_url
    assert admitted[0].revision_id == "source-v2"
    assert admitted[0].metadata == {
        "catalogue_access": "shared_read_only", "language": "fr",
    }

    # An authenticated session/borrower, even using a deceptive source ID,
    # never gets the anonymous exception that retains public image URLs.
    for session in (
        NS(provider_id="numilog-catalog", credential_id="__public__",
           credentials={"token": "secret"}, _accounts=[]),
        NS(provider_id="numilog-catalog", credential_id="__public__",
           credentials={}, _accounts=[object()]),
        NS(provider_id="numilog-catalog", credential_id="private",
           credentials={}, _accounts=[]),
    ):
        projected = _public_rows_from_response(response, session)
        assert len(projected) == 1
        assert projected[0].thumbnail_url is None
        assert projected[0].revision_id is None
        assert projected[0].metadata == admitted[0].metadata

    for mutation in (
        {"thumbnail_url": "https://covers.example.org/a.jpg?token=secret"},
        {"source_collection": "library"},
        {"source_account_id": "account-b"},
        {"owned": True},
        {"downloadable": True},
        {"download_url": "https://private.invalid/file"},
    ):
        altered = replace(raw, **mutation)
        snapshot = ProviderSearchResponse(
            results=[altered],
            public_notices=(PublicSearchNotice(altered),),
        )
        projected = _public_rows_from_response(snapshot, anonymous)
        if projected:
            assert projected[0].thumbnail_url is None
            assert projected[0].revision_id is None
            assert projected[0].download_url is None
            assert projected[0].source_account_id is None


def test_public_response_discards_unknown_private_metadata_even_if_cover_safe():
    from cifereca.models import SearchResult
    row = SearchResult(
        id="public:two", record_id="two", docbase="NUMILOG",
        medium="digital", title="A safe catalogue title", author=None,
        type="Book", format="EPUB", publisher=None, published_at=None,
        description=None, thumbnail_url="https://covers.example.org/catalogue.png",
        friendly_url=None, scenario="NUMILOG_PUBLIC",
        source_provider="numilog-catalog", source_credential="__public__",
        source_collection="public_catalog", owned=False,
        downloadable=False, download_url=None,
        metadata={"language": ["fr"], "account": "private-borrower"},
    )
    session = NS(
        provider_id="numilog-catalog", credential_id="__public__",
        credentials={}, _accounts=[],
    )
    shared = _public_rows_from_response(
        ProviderSearchResponse([row], public_notices=(PublicSearchNotice(row),)),
        session,
    )
    assert len(shared) == 1
    assert shared[0].thumbnail_url == row.thumbnail_url
    assert shared[0].metadata == {
        "catalogue_access": "shared_read_only", "language": ["fr"],
    }


def test_public_supplier_same_call_not_previous_page_and_no_duplicate_get(
    tmp_path, monkeypatch,
):
    service, session, calls = build(tmp_path, monkeypatch)
    try:
        live = session.search_with_response_facets("loup", scope="catalog")
        assert len(live.results) == len(live.public_notices) == 1
        assert live.public_notices[0].result is live.results[0]
        assert _public_rows_from_response(live, session)[0].thumbnail_url is not None
        first = live.results[0]
        alternate = replace(first)
        # A same-key but not same-object notice keeps generic public metadata
        # but does NOT qualify the anonymous URL/edition exception.
        mismatch = ProviderSearchResponse(
            results=[first], public_notices=(PublicSearchNotice(alternate),),
        )
        assert _public_rows_from_response(mismatch, session)[0].thumbnail_url is None
        second = session.search_with_response_facets("loup", scope="catalog")
        assert len(second.results) == 1 and len(calls) == 1
        assert second.public_notices[0].result is second.results[0]
    finally:
        service.close()
        session.close()
