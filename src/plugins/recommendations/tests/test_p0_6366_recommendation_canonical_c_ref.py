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


def _verified(db, *, published_at=None):
    raw = SearchResult(
        id="public-row", record_id="source-record-1", docbase="digital-books",
        medium="digital", title="Même titre", author="Même auteur",
        type="book", format="EPUB", publisher=None, published_at=published_at,
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


def test_raw_gtk_list_item_reuses_only_revalidated_c_cover(tmp_path):
    """E2E exact_setup passes BookListItem, not the public UiBookListEntry.

    Its N2 field is _cover_document_ref: absence of the public field must
    not crash the detail or authorize a stale/multi-edition cover.
    """
    db = Storage(tmp_path / "gtk-shape.sqlite")
    try:
        live = _verified(db)
        ranked = _ranked(live)
        view, _, _, _, _ = _view(ranked.notices)
        view._ranked = [ranked]
        view.host.public_notice_cover_ref = _host().public_notice_cover_ref
        view._matches = lambda candidate: candidate is ranked
        view._candidate_key = lambda _: "canonical:one"
        rows, details = [], []
        view._master.show_rows = lambda items, **kw: rows.append(tuple(items))
        view._master.show_detail = details.append
        view._render(preserve_summary=True)

        original_row = rows[-1][0]
        canonical = live._core_notice_canonical_cover_document_ref
        assert original_row.cover_document_ref == canonical
        # Mirrors BookListItem's public fields and *private* validated scope;
        # deliberately has no cover_document_ref public property.
        gtk_row = NS(
            key=original_row.key, title=original_row.title,
            subtitle=original_row.subtitle,
            thumbnail_url=original_row.thumbnail_url,
            _cover_document_ref=canonical,
        )
        assert not hasattr(gtk_row, "cover_document_ref")
        view._selected_row(gtk_row)
        assert details[-1].cover_document_ref == canonical
        assert view._exact_notice_state["row_presentation"][-1] == canonical

        gtk_row._cover_document_ref = object()
        view._selected_row(gtk_row)
        assert details[-1].cover_document_ref is None

        # Same GTK row, but the recommendation now aggregates two source
        # variants. The old documentary ref cannot survive revalidation.
        gtk_row._cover_document_ref = canonical
        view._ranked_by_key[gtk_row.key] = _ranked(
            live, variants=ranked.notices * 2,
        )
        view._selected_row(gtk_row)
        assert details[-1].cover_document_ref is None
        assert db.entitlements() == []
    finally:
        db.close()

def test_revision_aware_host_packet_forwarded_and_refreshes_neutral_detail(tmp_path):
    # The plugin operates solely on the new public binding port; it neither
    # interprets a Core receipt nor changes the standard detail contract.
    db = Storage(tmp_path / "opaque-receipt.sqlite")
    try:
        live = _verified(db)
        ranked = _ranked(live)
        view, _, _, _, _ = _view(ranked.notices)
        view._ranked = [ranked]
        view.host.public_notice_cover_ref = _host().public_notice_cover_ref
        view._exact_notice_state = None
        view._matches = lambda candidate: candidate is ranked
        view._candidate_key = lambda _: "canonical:one"
        document = live._core_notice_canonical_cover_document_ref
        packet = [object()]
        view.host.public_notice_cover_binding = (
            lambda item, *, identity_id: (document, packet[0])
        )
        rows, details = [], []
        view._master.show_rows = lambda items, **kw: rows.append(tuple(items))
        view._master.show_detail = details.append

        view._render(preserve_summary=True)
        first = rows[-1][0]
        assert first.cover_document_ref == document
        assert first.cover_document_receipt is packet[0]
        view._selected_row(first)
        assert details[-1].cover_document_ref == document
        assert view._exact_notice_state["cover_document_receipt"] is packet[0]

        # Identical visible title, author, URL and canonical ref; only the
        # opaque Core binding changes during a progressive redraw.
        packet[0] = object()
        previously_rendered = len(details)
        view._render(preserve_summary=True)
        assert rows[-1][0].cover_document_receipt is packet[0]
        assert len(details) == previously_rendered + 1
        assert view._exact_notice_state["cover_document_receipt"] is packet[0]
        assert details[-1].cover_document_ref == document

        # The new port's refusal must not be upgraded into a C by calling
        # the weaker compatibility port again.
        view.host.public_notice_cover_binding = lambda *args, **kw: None
        view._render(preserve_summary=True)
        assert rows[-1][0].cover_document_ref is None
        assert rows[-1][0].cover_document_receipt is None
        assert db.entitlements() == []
    finally:
        db.close()

def test_real_reopened_c_candidates_rank_and_render_without_supplier(tmp_path, monkeypatch):
    from cifereca.brokers import CatalogBrokerImpl
    from cifereca.core.notice_cover_repository import _opaque_key
    from cifereca.core.notice_c_recommendation_known import known_public_c_binding_facts
    from recommendation_service import compute_recommendations
    import recommendation_service

    filename = tmp_path / "offline-rank.sqlite"
    db = Storage(filename)
    try:
        live = _verified(db, published_at="2025")
        ref = live._core_notice_canonical_cover_document_ref
        epoch = live._core_notice_canonical_cover_document_epoch
        with db._lock, db._connect() as sql:
            sql.execute("DELETE FROM metadata WHERE key=?",
                        (_opaque_key(live._core_notice_cover_document_ref),))
    finally:
        db.close()
    restored = Storage(filename)
    try:
        delegate = CatalogBrokerImpl(NS(storage=restored))

        class OfflineCatalogue:
            known_public_notices = delegate.known_public_notices
            def searchable_providers(self):
                return []
            def search(self, *_a, **_kw):
                raise AssertionError("no provider session")
            search_progressive = search

        monkeypatch.setattr(recommendation_service, "build_queries",
                            lambda *a, **kw: ("Même", "titre"))
        library = NS(library_entries=lambda **kw: [
            {"title": "Autre livre", "author": "Même auteur"},
        ])
        result = compute_recommendations(
            library, OfflineCatalogue(), "reader-1",
            exclude_owned=False, max_queries=2,
        )
        assert len(result.ranked) == 1
        ranked = result.ranked[0]
        assert len(ranked.notices) == 1
        assert ranked.notices[0].item is ranked.item
        assert not ranked.notices[0].has_source_coordinates
        assert result.network_batches == result.cache_batches == 0
        assert result.catalogue_observations == ()
        assert known_public_c_binding_facts(ranked.item) == (ref, epoch)
        assert ranked.item.published_at == "2025"
        view, _, _, _, _ = _view(ranked.notices)
        opaque = object()
        view.host.public_notice_cover_ref = lambda item, **kw: (
            known_public_c_binding_facts(item) or (None, None)
        )[0]
        view.host.public_notice_cover_binding = lambda item, **kw: (
            (ref, opaque) if known_public_c_binding_facts(item) == (ref, epoch)
            else None
        )
        view._ranked = [ranked]
        view._exact_notice_state = None
        view._matches = lambda candidate: candidate is ranked
        view._candidate_key = lambda _: "offline:one"
        rows, details = [], []
        view._master.show_rows = lambda items, **kw: rows.append(tuple(items))
        view._master.show_detail = details.append
        view._render(preserve_summary=True)
        row = rows[-1][0]
        assert row.cover_document_ref == ref
        assert row.cover_document_receipt is opaque
        assert row.thumbnail_url is None
        view._selected_row(row)
        assert details[-1].cover_document_ref == ref
        assert any(field.label == "Publication" and field.value == "2025"
                   for section in details[-1].sections for field in section.fields)
        assert not ranked.notices[0].has_source_coordinates

        # Exercise the optional renderer port on the actual selection path.
        # This is a synthetic field receipt, not another documentary source.
        from cifereca.plugin_api import DetailField, Provenance
        field = DetailField("alternate", "Source 2 · bnf · Série", "Témoin",
                            provenance=Provenance("fixture", provider_id="bnf"))
        calls = []
        def project(candidate, **kwargs):
            calls.append((candidate, kwargs))
            return (field,)
        view.host.public_notice_alternative_fields = project
        view._selected_row(row)
        assert calls == [(ranked.item, {
            "identity_id": view.selected_identity_id(), "document": ref,
            "receipt": opaque, "catalogue_sources": view._selected_catalogue_sources(),
        })]
        alternatives, = [part for part in details[-1].sections
                         if part.id == "known-public-alternatives"]
        assert alternatives.fields == (field,)
        view.host.public_notice_alternative_fields = lambda *a, **kw: ()
        view._selected_row(row)
        assert not any(part.id == "known-public-alternatives"
                       for part in details[-1].sections)
        # The primary age is another optional, strictly source-bound CoreUI
        # port. The plugin forwards the same C packet; it must never parse
        # youth_age_ranges or invent a fallback when Core refuses it.
        age = DetailField(
            "known-public-primary-age-1", "Âge éditeur", "À partir de 12 ans",
            kind="copy", provenance=Provenance(
                "fixture.age", provider_id="bnf", source_id="source-record-1",
            ),
        )
        age_calls = []
        def project_age(candidate, **kwargs):
            age_calls.append((candidate, kwargs))
            return (age,)
        view.host.public_notice_primary_age_fields = project_age
        view._selected_row(row)
        assert age_calls == [(ranked.item, {
            "identity_id": view.selected_identity_id(), "document": ref,
            "receipt": opaque, "catalogue_sources": view._selected_catalogue_sources(),
        })]
        primary, = [part for part in details[-1].sections
                    if part.id == "known-public-primary-age"]
        assert primary.fields == (age,)
        view.host.public_notice_primary_age_fields = lambda *a, **kw: ()
        view._selected_row(row)
        assert not any(part.id == "known-public-primary-age"
                       for part in details[-1].sections)
        ranked.item.published_at = "2024"
        assert known_public_c_binding_facts(ranked.item) is None
        assert restored.entitlements() == []
    finally:
        restored.close()
