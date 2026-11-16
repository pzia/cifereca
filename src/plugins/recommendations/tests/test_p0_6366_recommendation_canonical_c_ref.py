"""#6366: Recommendations list/detail reuse one witnessed canonical public C.

An affinity/ISBN cluster is NOT an edition relation. The only path to the
shared N2 ref here is the original, same-call, Core-attested public SearchResult.
"""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace as NS

from cifereca.core.notice_c_cover_index import read_consolidated_notice_cover
from cifereca.core.notice_c_known_projection import matching_live_public_canonical_ref
from cifereca.core.search_orchestration import (
    _public_rows_from_response, _mirror_remote_results,
)
from cifereca.models import SearchResult
from cifereca.plugin_api import ProviderSearchResponse, PublicSearchNotice
from cifereca.storage import Storage
from recommendation_engine import CatalogNoticeVariant, RankedCandidate
from recommendations import _attested_recommendation_c_cover

from .recommendations_catalogue_test_fixtures import _view


def _host():
    return NS(public_notice_cover_ref=lambda item, *, identity_id:
              matching_live_public_canonical_ref(
                  item, identity_id=identity_id,
              ))


def _ranked(item, variants=None):
    variant = CatalogNoticeVariant(
        item=item, source_provider=item.source_provider,
        source_credential=item.source_credential,
        record_id=item.record_id, docbase=item.docbase,
        source_collection=item.source_collection, isbn="",
    )
    return RankedCandidate(
        item=item, score=83, reasons=("Catalogue public exact",),
        sources=(item.source_provider,),
        notices=(variant,) if variants is None else variants,
    )


def _verified(db):
    raw = SearchResult(
        id="public-row", record_id="source-record-1", docbase="digital-books",
        medium="digital", title="Même titre", author="Même auteur",
        type="book", format="EPUB", publisher=None, published_at=None,
        description=None,
        thumbnail_url="https://covers.example.org/exact.jpg",
        friendly_url=None, scenario="search",
        source_provider="bnf", source_credential="__public__",
        source_collection="public_catalog", source_account_id=None,
        owned=False, downloadable=False, download_url=None, metadata={},
    )
    session = NS(
        provider_id="bnf", credential_id="__public__", credentials={},
        _accounts=[],
    )
    rows = _public_rows_from_response(
        ProviderSearchResponse(
            results=[raw], public_notices=(PublicSearchNotice(raw),),
        ), session,
    )
    assert len(rows) == 1 and rows[0] is not raw
    row = rows[0]
    assert _mirror_remote_results(
        NS(storage=db), [row], identity_id="alice",
        require_public_cover_witness=True,
    ) == 1
    assert read_consolidated_notice_cover(
        db, row._core_notice_canonical_cover_document_ref,
    ) is not None
    return row


def test_witnessed_search_result_populates_real_recommendations_list_and_detail(
    tmp_path,
):
    db = Storage(tmp_path / "book.sqlite")
    try:
        live = _verified(db)
        ranked = _ranked(live)
        view, _, _, _, _ = _view(ranked.notices)
        view._ranked = [ranked]
        view.host.public_notice_cover_ref = _host().public_notice_cover_ref
        view._exact_notice_state = None
        view._matches = lambda candidate: candidate is ranked
        view._candidate_key = lambda _: "canonical:one"
        rows, details = [], []
        view._master.show_rows = lambda items, **kw: rows.append(tuple(items))
        view._master.show_detail = details.append

        view._render(preserve_summary=True)
        assert len(rows[-1]) == 1
        one = rows[-1][0]
        canonical = live._core_notice_canonical_cover_document_ref
        assert one.cover_document_ref == canonical
        assert one.thumbnail_url == live.thumbnail_url
        view._selected_row(one)
        assert len(details) == 1
        assert details[0].cover_document_ref == canonical
        assert view._exact_notice_state["row_presentation"] == (
            one.title, one.subtitle, one.thumbnail_url, canonical,
        )

        # No supplier re-query; the raw thumbnail is only the legacy
        # fallback and MUST NOT own the documentary N2 decision.
        assert _attested_recommendation_c_cover(
            ranked, host=view.host, identity_id="reader-1",
            catalogue_sources=(),
        ) == canonical
        assert db.entitlements() == []
    finally:
        db.close()


def test_ambiguous_aggregates_cache_replays_and_wrong_scope_never_borrow_c(
    tmp_path,
):
    db = Storage(tmp_path / "other.sqlite")
    try:
        live = _verified(db)
        original = _ranked(live)
        ref = live._core_notice_canonical_cover_document_ref
        assert _attested_recommendation_c_cover(
            original, host=_host(), identity_id="bob",
            catalogue_sources=(),
        ) == ref

        def check_none(ranked, sources=()):
            assert _attested_recommendation_c_cover(
                ranked, host=_host(), identity_id="bob",
                catalogue_sources=sources,
            ) is None

        check_none(original, ("kobo",))
        check_none(replace(original, sources=("bnf", "kobo")))
        check_none(replace(original, notices=original.notices * 2))
        check_none(_ranked(replace(live)))  # detached cache copy, no witness
        check_none(_ranked(replace(live, record_id="foreign")))
        check_none(_ranked(replace(live, thumbnail_url="https://covers.example.org/other.jpg")))
        foreign = replace(original.notices[0], record_id="another-edition")
        check_none(_ranked(live, (foreign,)))
        other_object = replace(live)
        check_none(_ranked(live, (replace(original.notices[0], item=other_object),)))
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        db.close()
