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
        # An actually parsed supplier, not a synthetic SearchResult, must
        # feed the canonical C index under its exact source edition.
        from cifereca.core.notice_c_cover_index import (
            canonical_cover_document_ref, read_consolidated_notice_cover,
        )
        from cifereca.core.notice_c_public_search import search_public_notice_c
        from cifereca.gui.notice_cover_cached_reader import cached_notice_cover_snapshot
        exact_edition = persisted.observation.key.edition_id
        canonical = canonical_cover_document_ref(exact_edition)
        assert a._core_notice_canonical_cover_document_ref == canonical
        admitted = read_consolidated_notice_cover(service.storage, canonical)
        assert admitted is not None and len(admitted.sources) == 1
        assert [x.url for x in admitted.candidates] == [a.thumbnail_url]
        n2 = cached_notice_cover_snapshot(NS(storage=service.storage), canonical)
        assert n2.scope == canonical and n2.revision == admitted.epoch
        known_page = search_public_notice_c(service.storage, "loup")
        assert any(hit.document == canonical for hit in known_page.hits)

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
        first = live.results[0]
        projected = _public_rows_from_response(live, session)[0]
        assert projected.thumbnail_url is not None
        assert first.friendly_url is not None
        marker = f"{session.provider_id}.public_search_product_card"
        assert first.metadata["public_url_evidence"] == marker
        assert projected.friendly_url == first.friendly_url
        assert projected.metadata["public_url_evidence"] == marker
        for bad in (
            replace(first, metadata={**first.metadata,
                                     "public_url_evidence": "numilog.public_search_product_card"}),
            replace(first, friendly_url="https://www.numilog.com/search"),
            replace(first, friendly_url=first.friendly_url + "?token=secret"),
        ):
            result, = _public_rows_from_response(
                ProviderSearchResponse([bad], public_notices=(PublicSearchNotice(bad),)),
                session,
            )
            assert result.friendly_url is None
            assert "public_url_evidence" not in result.metadata
        private = NS(provider_id=session.provider_id, credential_id="private",
                     credentials={"test": "not-public"}, _accounts=[])
        result, = _public_rows_from_response(live, private)
        assert result.friendly_url is None
        assert "public_url_evidence" not in result.metadata
        alternate = replace(first)
        # A same-key but not same-object notice keeps generic public metadata
        # but does NOT qualify the anonymous URL/edition exception.
        mismatch = ProviderSearchResponse(
            results=[first], public_notices=(PublicSearchNotice(alternate),),
        )
        rejected = _public_rows_from_response(mismatch, session)[0]
        assert rejected.thumbnail_url is None and rejected.friendly_url is None
        assert "public_url_evidence" not in rejected.metadata
        second = session.search_with_response_facets("loup", scope="catalog")
        assert len(second.results) == 1 and len(calls) == 1
        assert second.public_notices[0].result is second.results[0]
    finally:
        service.close()
        session.close()


def test_actual_anonymous_federated_cache_shared_across_a_b_a_without_new_supplier_call(
    tmp_path, monkeypatch,
):
    """The reader is not a source-account namespace for a public catalogue.

    Both sync and progressive caches are currently separate, but EACH must
    be reusable by a second reader with the same ranking preferences.
    A cache replay is only a source coordinate; Notice C remains its proof.
    """
    service, session, supplier_gets = build(tmp_path, monkeypatch)
    calls = []
    method = session.search_with_response_facets

    def count(*args, **kwargs):
        calls.append((args, kwargs))
        return method(*args, **kwargs)

    monkeypatch.setattr(session, "search_with_response_facets", count)
    try:
        a = service.search("loup", provider_id=session.provider_id, force=True)
        assert len(a) == 1
        document = a[0]._core_notice_cover_document_ref
        assert len(calls) == len(supplier_gets) == 1
        warning, = service.last_search_warnings
        assert "pagination" in warning

        service.select_identity("bob")
        b = service.search("loup", provider_id=session.provider_id, force=False)
        assert len(b) == 1
        assert b[0]._core_notice_cover_document_ref == document
        assert len(calls) == 1  # Not just a plugin HTTP cache HIT.
        assert service.last_search_warnings == [warning]
        assert read_notice_cover(service.storage, document).epoch == 1

        progressive = service.search_progressive(
            "loup", provider_id=session.provider_id, force=True,
        )
        assert not progressive.cancelled and progressive.result_count == 1
        assert progressive.providers_failed == 0
        assert progressive.warnings == (warning,)
        assert len(calls) == 2  # Existing sync/progressive source caches differ.

        service.select_identity("alice")
        batches = []
        result = service.search_progressive(
            "loup", provider_id=session.provider_id, force=False,
            on_batch=batches.append,
        )
        assert not result.cancelled
        assert batches and batches[0].cached is True
        assert batches[0].warnings == (warning,) and not batches[0].error_code
        assert batches[0].result_status == "partial"
        assert result.warnings == (warning,) and result.providers_failed == 0
        assert service.last_search_warnings == [warning]
        assert batches[0].results[0]._core_notice_cover_document_ref == document
        assert len(calls) == 2  # Public progressive result cache reused.
        assert len(supplier_gets) == 1
        assert read_notice_cover(service.storage, document).epoch == 1
        # Same source, different viewer. No source credentials/account rights.
        assert b[0].source_account_id is None
        assert b[0].downloadable is False and b[0].owned is False
    finally:
        service.close()
        session.close()


def test_cache_shared_only_for_exact_sole_anonymous_source_and_same_ranking(
    tmp_path, monkeypatch,
):
    from cifereca.core.search_orchestration import _search_presentation_cache_scope

    service, session, _calls = build(tmp_path, monkeypatch)
    try:
        a = service.identity_by_id("alice")
        b = service.identity_by_id("bob")
        assert _search_presentation_cache_scope(
            a, [session], ((session, None),),
        ) == _search_presentation_cache_scope(
            b, [session], ((session, None),),
        )
        # A private or account-reassigned session never gets a shared key.
        assert _search_presentation_cache_scope(
            a, [session], ((session, "alice"),),
        ) != _search_presentation_cache_scope(
            b, [session], ((session, "bob"),),
        )
        mixed = NS(provider_id="private", credential_id="credential-b")
        assert _search_presentation_cache_scope(
            a, [session, mixed], ((session, None), (mixed, "bob")),
        ) != _search_presentation_cache_scope(
            b, [session, mixed], ((session, None), (mixed, "bob")),
        )
        private = NS(
            provider_id=session.provider_id, credential_id="__public__",
            credentials={"secret": "redacted"}, _accounts=[],
            search_with_response_facets=session.search_with_response_facets,
        )
        assert _search_presentation_cache_scope(
            a, [private], ((private, None),),
        ) != _search_presentation_cache_scope(
            b, [private], ((private, None),),
        )
        # Reordering depends on reading preferences: the cache cannot reuse
        # a previously ordered presentation for a different reader ranking.
        pref_fr = NS(
            identity_id="a", preferences=NS(
                reading_languages=("fr",), prefer_original_language=False,
            ),
        )
        pref_en = NS(
            identity_id="b", preferences=NS(
                reading_languages=("en",), prefer_original_language=True,
            ),
        )
        assert _search_presentation_cache_scope(
            pref_fr, [session], ((session, None),),
        ) != _search_presentation_cache_scope(
            pref_en, [session], ((session, None),),
        )
        # An identity literally named as the anonymous key prefix cannot
        # collide because that source-specific prefix begins with NUL.
        adversary = NS(
            identity_id="source-anonymous-public-v1",
            preferences=pref_fr.preferences,
        )
        assert _search_presentation_cache_scope(
            pref_fr, [session], ((session, None),),
        ) != _search_presentation_cache_scope(
            adversary, [private], ((private, None),),
        )
    finally:
        service.close()
        session.close()


def test_observed_numilog_federated_two_source_revisions_survive_search_and_c(
    tmp_path, monkeypatch,
):
    """Future supplier revisions cannot collapse before persisted Notice C.

    The first result is parsed from the observed HTML fixture by the REAL
    provider. We then simulate an upstream response containing two revision
    markers for that one source record; their common image is not a join.
    """
    service, session, supplier_gets = build(tmp_path, monkeypatch)
    try:
        observed = session.search_with_response_facets(
            "loup", scope="catalog",
        )
        assert len(observed.results) == 1
        original = observed.results[0]
        first = replace(original, revision_id="upstream-rev-1")
        second = replace(original, revision_id="upstream-rev-2")
        envelope = ProviderSearchResponse(
            results=[first, second],
            public_notices=(
                PublicSearchNotice(first), PublicSearchNotice(second),
            ),
        )
        monkeypatch.setattr(
            session, "search_with_response_facets",
            lambda *args, **kwargs: envelope,
        )
        results = service.search(
            "loup", provider_id=session.provider_id, force=True,
        )
        assert len(results) == 2
        assert {r.revision_id for r in results} == {
            "upstream-rev-1", "upstream-rev-2",
        }
        refs = [r._core_notice_cover_document_ref for r in results]
        assert len(set(refs)) == 2
        assert all(ref.identity_id is None for ref in refs)
        assert len({r.record_id for r in results}) == 1
        assert len({r.thumbnail_url for r in results}) == 1
        assert all(read_notice_cover(service.storage, ref).epoch == 1 for ref in refs)

        # Search cache contains both identities and restores their distinct
        # Notice C refs from exact persisted coordinates, not an image URL.
        cached = service.search(
            "loup", provider_id=session.provider_id, force=False,
        )
        assert len(cached) == 2
        assert [r._core_notice_cover_document_ref for r in cached] == refs

        service.select_identity("bob")
        batches = []
        summary = service.search_progressive(
            "loup", provider_id=session.provider_id, force=True,
            on_batch=batches.append,
        )
        assert not summary.cancelled and summary.result_count == 2
        assert batches and len(batches[0].results) == 2
        assert {
            r._core_notice_cover_document_ref
            for r in service.last_search
        } == set(refs)
        assert all(read_notice_cover(service.storage, ref).epoch == 1 for ref in refs)
        assert supplier_gets == [1]  # No extra provider HTML fetch.
        assert service.storage.entitlements() == []
        assert service.storage.acquisitions() == []
    finally:
        service.close()
        session.close()


def test_observed_source_cache_and_search_ttl_expire_but_notice_c_remains(
    tmp_path, monkeypatch,
):
    """P0: the short-lived source/HTTP/search caches are NOT a Notice C TTL.

    This exercises a real parsed Numilog HTML response and a live Core
    federated producer, then purges both the provider's source page cache and
    the SQLite search-cache namespace. C remains in the SAME SQLite store,
    including after a >24h clock advance and full process-style restart.
    Neither cache invalidation creates a right or performs an image GET.
    """
    import cifereca.core.search_orchestration as orchestration
    from cifereca.gui.notice_cover_cached_reader import cached_notice_cover_snapshot

    clock = [1_800_000_000]
    monkeypatch.setattr(orchestration.time, "time", lambda: clock[0])
    service, session, supplier_gets = build(tmp_path, monkeypatch)
    path = tmp_path / "notices.sqlite"
    try:
        rows = service.search(
            "loup", provider_id=session.provider_id, force=True,
        )
        assert rows and supplier_gets == [1]
        first = rows[0]
        document = first._core_notice_cover_document_ref
        initial = read_notice_cover(service.storage, document)
        assert initial is not None and initial.epoch == 1
        assert initial.attribute.candidates[0].url == first.thumbnail_url
        assert any(
            namespace["namespace"] == "search" and namespace["entries"] > 0
            for namespace in service.storage.cache_namespace_stats()
        )

        # Real provider's ephemeral parsed-page cache (the HTTP response is
        # already consumed); clearing it cannot delete metadata-owned C.
        session._public_cache.clear()
        clock[0] += 25 * 3600
        assert service.storage.purge_expired() >= 1
        assert not any(
            namespace["namespace"] == "search" and namespace["entries"] > 0
            for namespace in service.storage.cache_namespace_stats()
        )
        surviving = read_notice_cover(service.storage, document)
        assert surviving == initial
        assert cached_notice_cover_snapshot(
            NS(storage=service.storage), document,
        ).candidates[0].url == first.thumbnail_url
        assert supplier_gets == [1]  # A local C read needs no provider GET.
        assert service.storage.entitlements() == []
        assert service.storage.acquisitions() == []

        # Explicit refresh may now reobserve the same actual source. This
        # advances its last-seen watermark, not the N2 epoch for identical C.
        refreshed = service.search(
            "loup", provider_id=session.provider_id, force=True,
        )
        assert refreshed and supplier_gets == [1, 1]
        assert refreshed[0]._core_notice_cover_document_ref == document
        assert read_notice_cover(service.storage, document).epoch == 1
    finally:
        service.close()
        session.close()

    restarted = Storage(path)
    try:
        persisted = read_notice_cover(restarted, document)
        assert persisted is not None and persisted.epoch == 1
        assert persisted.attribute.candidates[0].url == first.thumbnail_url
        assert cached_notice_cover_snapshot(
            NS(storage=restarted), document,
        ).revision == 1
        assert restarted.entitlements() == []
        assert restarted.acquisitions() == []
        assert supplier_gets == [1, 1]
    finally:
        restarted.close()


def test_real_numilog_search_writes_public_bibliography_inside_same_notice_c(
    tmp_path, monkeypatch,
):
    """SQLite Notice C, not the per-profile mirror, serves a second viewer."""
    from cifereca.core.notice_c_public_search import search_public_notice_c
    from cifereca.gui.notice_cover_cached_reader import cached_notice_cover_snapshot

    service, session, supplier_gets = build(tmp_path, monkeypatch)
    storage_path = tmp_path / "notices.sqlite"
    try:
        rows = service.search(
            "loup", provider_id=session.provider_id, force=True,
        )
        assert rows and supplier_gets == [1]
        original = rows[0]
        document = original._core_notice_cover_document_ref
        persisted = read_notice_cover(service.storage, document)
        assert persisted is not None
        assert persisted.observation.parser_version == "2"
        facts = persisted.observation.fields_copy()["search_result"]
        assert facts["public_bibliography"]["title"] == original.title
        assert facts["public_bibliography"]["record_id"] == original.record_id
        assert facts["thumbnail_url"] == original.thumbnail_url
        assert facts["public_web_references"] == {
            "schema": 1, "links": [{
                "url": original.friendly_url,
                "evidence": f"{session.provider_id}.public_search_product_card",
            }],
        }

        page = service.search_public_notice_c(
            "loup", provider_ids=(session.provider_id,),
        )
        assert page.status == "partial"  # No false complete-query claim.
        assert len(page.hits) == 1
        first = page.hits[0]
        from cifereca.core.notice_c_cover_index import canonical_cover_document_ref
        canonical = canonical_cover_document_ref(persisted.observation.key.edition_id)
        assert original._core_notice_canonical_cover_document_ref == canonical
        from cifereca.core.notice_c_cover_index import read_consolidated_notice_cover
        web, = read_consolidated_notice_cover(service.storage, canonical).catalogue_web_references
        assert web.url == original.friendly_url
        assert web.provider_id == session.provider_id and web.record_id == original.record_id
        assert web.source_ref == document.source_key
        assert first.document == canonical
        assert first.title == original.title
        assert first.record_id == original.record_id
        assert first.cover_state == "available"
        assert first.epoch == persisted.epoch == 1
        assert first.stale is False
        assert cached_notice_cover_snapshot(
            NS(storage=service.storage), first.document,
        ).candidates[0].url == original.thumbnail_url

        service.storage.cache_delete_namespace("search")
        session._public_cache.clear()
        service.select_identity("bob")
        second = service.search_public_notice_c("loup")
        assert second.hits == page.hits
        assert supplier_gets == [1]
        # Genuine parsed Numilog C must survive the *provider-source*
        # metadata purge, not only a Search HTTP cache clear. The same
        # canonical N2 provenance is retained across the service reboot.
        from cifereca.core.notice_cover_repository import _opaque_key
        with service.storage._lock, service.storage._connect() as conn:
            conn.execute(
                "DELETE FROM metadata WHERE key=?",
                (_opaque_key(document),),
            )
        assert read_notice_cover(service.storage, document) is None
        after_source_purge = service.search_public_notice_c("loup")
        assert len(after_source_purge.hits) == 1
        assert after_source_purge.hits[0].document == canonical
        assert after_source_purge.hits[0].title == original.title
        assert supplier_gets == [1]
        # Availability, ownership, account and provider action remain absent.
        assert not hasattr(first, "download_url")
        assert not hasattr(first, "owned")
        assert service.storage.entitlements() == []
        assert service.storage.acquisitions() == []
    finally:
        service.close()
        session.close()
    db = Storage(storage_path)
    try:
        page = search_public_notice_c(db, "loup")
        assert len(page.hits) == 1 and page.hits[0].document == canonical
        assert page.hits[0].title == original.title
        assert read_consolidated_notice_cover(db, canonical).catalogue_web_references == (web,)
        # +25h does not DELETE C; it marks its observed facts stale.
        stale = search_public_notice_c(
            db, "loup", now=persisted.observation.observed_at + 25 * 3600,
        )
        assert stale.hits and stale.hits[0].stale is True
        assert stale.hits[0].document == canonical
        assert supplier_gets == [1]
    finally:
        db.close()


def test_real_numilog_notice_c_is_first_known_search_across_profiles_no_get(
    tmp_path, monkeypatch,
):
    """Observed supplier HTML -> C source -> known Search list/detail source.

    This is the actual asynchronous Search worker's Core API, independent of
    the legacy per-viewer catalogue mirror and public page cache. GTK never
    needs a supplier call, owner session or guessed URL to show the C cover.
    """
    service, session, supplier_gets = build(tmp_path, monkeypatch)
    try:
        observed = service.search(
            "loup", provider_id=session.provider_id, force=True,
        )
        assert observed and supplier_gets == [1]
        c_ref = observed[0]._core_notice_cover_document_ref
        assert read_notice_cover(service.storage, c_ref) is not None
        canonical = observed[0]._core_notice_canonical_cover_document_ref
        assert canonical != c_ref

        # A public-only source is shared across readers but never shares
        # rights. Bob has no per-profile historical mirror of Alice's row.
        service.storage.cache_delete_namespace("search")
        session._public_cache.clear()
        service.select_identity("bob")
        known = service.search_known_catalog("loup", identity_id="bob")
        public = [r for r in known if r.get("source") == "notice_c_public"]
        assert len(public) == 1
        assert public[0]["_cover_document_ref"] == canonical
        assert public[0]["thumbnail_url"] is None
        assert public[0]["metadata"]["notice_c_query_status"] == "partial"
        assert public[0]["identity_id"] == "bob"
        assert public[0]["state"] == "remote_catalog"
        assert not public[0].get("download_url")
        assert not public[0].get("placement_id")
        assert supplier_gets == [1]
        assert service.storage.entitlements() == []
        assert service.storage.acquisitions() == []
        # An offline known C read never launches a new online session.
        assert supplier_gets == [1]
    finally:
        service.close()
        session.close()


def test_web_reference_witness_cannot_be_reused_after_url_or_marker_mutation(tmp_path, monkeypatch):
    from cifereca.core.notice_search_cover_producer import admit_public_search_catalogue
    from cifereca.core.search_orchestration import _verified_public_web_reference

    service, session, calls = build(tmp_path, monkeypatch)
    try:
        raw = session.search_with_response_facets("loup", scope="catalog")
        def projected():
            return _public_rows_from_response(raw, session)[0]
        def facts(row):
            item = admit_public_search_catalogue(
                row, identity_id="alice", observed_at=100, public_cover_url=row.thumbnail_url,
            )
            return item.fields_copy()["search_result"]
        good = projected()
        assert _verified_public_web_reference(good)["url"] == good.friendly_url
        assert "public_web_references" in facts(good)
        altered = projected()
        altered.friendly_url = "https://www.numilog.com/other-book"
        assert _verified_public_web_reference(altered) is None
        assert "public_web_references" not in facts(altered)
        altered = projected()
        altered.metadata["public_url_evidence"] = "other.public_search_product_card"
        assert "public_web_references" not in facts(altered)
        altered = projected()
        altered.record_id = "foreign-record"
        assert "public_web_references" not in facts(altered)
        # Dataclass reconstruction (e.g. cache replay) cannot recreate the token.
        assert _verified_public_web_reference(replace(good)) is None
        assert calls == [1]
    finally:
        service.close()
        session.close()


def test_numilog_web_reference_without_image_is_positive_but_cover_pending(tmp_path, monkeypatch):
    import re
    from cifereca.core.notice_c_cover_index import read_consolidated_notice_cover
    from cifereca.core.notice_c_web_choices import read_public_web_choices, resolve_public_web_choice
    from cifereca.core.notice_cover_repository import evict_notice_cover, persist_notice_cover
    from cifereca.core.notice_search_cover_producer import admit_public_search_catalogue

    service, session, calls = build(tmp_path, monkeypatch)
    try:
        without = re.sub(r'<img\b[^>]*>', '', FIXTURE.read_text(encoding="utf-8"))
        parsed = parse_public_search(without, SOURCE)
        assert parsed.items[0].thumbnail_url is None
        monkeypatch.setattr(numilog, "fetch_public_search",
                            lambda *a, **k: calls.append(1) or parsed)
        row, = service.search("loup", provider_id=session.provider_id, force=True)
        assert row.thumbnail_url is None and row.friendly_url
        doc = row._core_notice_cover_document_ref
        canonical = row._core_notice_canonical_cover_document_ref
        source = read_notice_cover(service.storage, doc)
        assert source.observation.status == "partial"
        assert source.attribute.state == "pending" and not source.attribute.candidates
        c = read_consolidated_notice_cover(service.storage, canonical)
        assert not c.snapshot_complete and not c.candidates
        web, = c.catalogue_web_references
        assert web.url == row.friendly_url
        menu = read_public_web_choices(service.storage, canonical)
        choice, = menu.choices
        def resolve():
            return resolve_public_web_choice(service.storage, canonical,
                expected_epoch=menu.epoch, choice_id=choice.choice_id)
        assert resolve() == row.friendly_url
        assert evict_notice_cover(service.storage, doc, expected_epoch=source.epoch)
        assert resolve() == row.friendly_url
        assert calls == [1]
        assert service.storage.entitlements() == [] and service.storage.acquisitions() == []

        # Rehydrate a completed observation, then retain it against a later
        # partial observation even if the latter has another qualified link.
        complete_page = parse_public_search(FIXTURE.read_text(encoding="utf-8"), SOURCE)
        full, = session._public_rows(complete_page.items)
        full = _public_rows_from_response(
            ProviderSearchResponse([full], public_notices=(PublicSearchNotice(full),)), session,
        )[0]
        observation = admit_public_search_catalogue(full, identity_id="alice",
            observed_at=source.observation.observed_at+1, public_cover_url=full.thumbnail_url)
        installed = persist_notice_cover(service.storage, observation,
            field_path=("search_result", "thumbnail_url"), cover_inventory_complete=True)
        assert installed.attribute.state == "available"
        # Parse a new card with the same supplier ID, not a forged DTO marker.
        changed = without.replace("du-loup.ebook", "du-loup-corrige.ebook")
        partial_page = parse_public_search(changed, SOURCE)
        partial_raw, = session._public_rows(partial_page.items)
        partial = _public_rows_from_response(
            ProviderSearchResponse([partial_raw], public_notices=(PublicSearchNotice(partial_raw),)), session,
        )[0]
        observation = admit_public_search_catalogue(partial, identity_id="alice",
            observed_at=source.observation.observed_at+2, public_cover_url=None)
        assert observation.fields_copy()["search_result"]["public_web_references"]["links"][0]["url"] != web.url
        retained = persist_notice_cover(service.storage, observation,
            field_path=("search_result", "thumbnail_url"), cover_inventory_complete=True)
        assert retained == installed
        assert resolve() is None
    finally:
        service.close()
        session.close()


def test_real_partial_failure_is_not_cached_as_a_paginated_window(tmp_path, monkeypatch):
    service, session, supplier_gets = build(tmp_path, monkeypatch)
    observed = session.search_with_response_facets
    calls = []

    def degraded(*args, **kwargs):
        calls.append(1)
        return replace(observed(*args, **kwargs), partial=True)

    monkeypatch.setattr(session, "search_with_response_facets", degraded)
    try:
        for force in (True, False):
            rows = service.search("loup", provider_id=session.provider_id, force=force)
            assert len(rows) == 1
            assert any("résultats partiels" in w for w in service.last_search_warnings)
            assert any("pagination" in w for w in service.last_search_warnings)
        assert len(calls) == 2
        for force in (True, False):
            batches = []
            result = service.search_progressive("loup", provider_id=session.provider_id,
                                                force=force, on_batch=batches.append)
            assert result.result_count == 1
            batch, = batches
            assert not batch.cached and batch.result_status == "partial"
            assert any("résultats partiels" in w for w in batch.warnings)
            assert any("pagination" in w for w in batch.warnings)
        assert len(calls) == 4 and len(supplier_gets) == 1
        assert not any(ns["namespace"] == "search" and ns["entries"] > 0
                       for ns in service.storage.cache_namespace_stats())
    finally:
        service.close()
        session.close()
