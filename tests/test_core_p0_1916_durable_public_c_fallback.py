"""#1916: exact installation-wide public C is a read-only stale fallback."""
from cifereca.core.known_catalog_fallback import known_public_fallback_page
from cifereca.core import known_catalog_fallback
from cifereca.core.notice_cover_repository import persist_notice_cover
from cifereca.storage import Storage
from tests.test_core_p0_6366_canonical_bibliography_retention import (
    _make_c, _drop_short_source,
)
from tests.test_core_p0_6366_public_c_sqlite_source_search import verified_public
from tests.test_core_p1_2385_notice_cover_persistence import PRIVATE, notice


def page(db, *, identity="viewer-a", provider="bnf", query="maritime",
         now=100, scope="digital", types=("epub",), **kw):
    return known_public_fallback_page(
        db, identity_id=identity, provider_id=provider, query=query,
        scope=scope, field="title", content_types=types, now=now, **kw,
    )


def test_durable_public_c_survives_source_eviction_and_profile_selection(tmp_path):
    path = tmp_path / "fallback.sqlite"
    db = Storage(path)
    try:
        source, _ = _make_c(db, verified_public(
            record_id="same-record", revision_id="edition-a",
            title="Voyage maritime", format="EPUB", description="Texte public",
        ))
        variant, _ = _make_c(db, verified_public(
            record_id="same-record", revision_id="edition-b",
            title="Voyage maritime", format="PDF",
        ))
        _drop_short_source(db, source)
        _drop_short_source(db, variant)
        persist_notice_cover(
            db, notice(key=PRIVATE), field_path=("media", "cover"),
            cover_inventory_complete=True,
        )
        assert db.catalog_entries() == []
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        db.close()
    db = Storage(path)
    try:
        for viewer in ("viewer-a", "viewer-b", "viewer-c"):
            observed = page(db, identity=viewer)
            assert observed.status == "known_catalog_stale"
            assert observed.matched_count == 1 and not observed.has_more
            row, = observed.results
            assert row.record_id == "same-record" and row.revision_id == "edition-a"
            assert row.docbase == source.observation.fields_copy()["search_result"]["public_bibliography"]["docbase"]
            assert row.description == "Texte public"
            assert row.source_credential == "known-public"
            assert row.metadata["notice_c_query_status"] == "partial"
            assert row.metadata["search_provenance"] == "known_catalog_stale"
            assert row.metadata["mirrored_at"] == 100
            assert row.owned is False and row.downloadable is False
            assert row.thumbnail_url is row.friendly_url is row.download_url is None
        both = page(db, types=(), page_size=1)
        assert both.matched_count == 2 and both.has_more
        second = page(db, types=(), page_size=1, page=1)
        assert second.matched_count == 2 and not second.has_more
        assert {both.results[0].revision_id, second.results[0].revision_id} == {"edition-a", "edition-b"}
        assert page(db, types=("mp3",)).status == "no_known_results"
        assert page(db, scope="physical").status == "no_known_results"
        assert page(db, provider="foreign").status == "no_known_results"
        assert page(db, now=99).status == "no_known_results"
        assert page(db, now=100+91*86400).status == "no_known_results"
        assert page(db, identity=None).status == "unavailable"
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        db.close()


def test_partial_type_filtered_cohort_never_claims_a_total(tmp_path, monkeypatch):
    db = Storage(tmp_path / "budget.sqlite")
    try:
        monkeypatch.setattr(known_catalog_fallback, "_MAX_RESULTS", 2)
        for idx in range(3):
            _make_c(db, verified_public(
                record_id=f"record-{idx}", title="Voyage maritime",
                format="EPUB" if idx == 0 else "PDF",
            ))
        result = page(db, page_size=1)
        assert result.status == "scope_budget_exceeded"
        assert result.results == () and result.matched_count == 0
    finally:
        db.close()


def test_read_error_distinguished_from_missing_public_facts(tmp_path, monkeypatch):
    from cifereca.core import notice_c_public_search
    db = Storage(tmp_path / "read-error.sqlite")
    try:
        def fail(*args, **kwargs):
            raise RuntimeError("PRIVATE_DIAGNOSTIC")
        monkeypatch.setattr(notice_c_public_search, "search_public_notice_c", fail)
        result = page(db)
        assert result.status == "unavailable" and result.results == ()
        assert "PRIVATE_DIAGNOSTIC" not in repr(result)
    finally:
        db.close()


def test_public_c_precedes_viewer_mirror_even_on_later_page(tmp_path, monkeypatch):
    db = Storage(tmp_path / "c-first.sqlite")
    try:
        source, _ = _make_c(db, verified_public(
            record_id="durable", title="Voyage maritime", format="EPUB",
            published_at="2020",
        ))
        _drop_short_source(db, source)
        db.catalog_entry_put({
            "catalog_entry_id": "legacy", "identity_id": "viewer-a",
            "provider_id": "bnf", "item_id": "legacy-record",
            "title": "Voyage maritime ancien", "media_format": "EPUB",
            "local_state": "remote_catalog",
            "metadata": {"source_collection": "catalog", "mirrored_at": 100,
                         "medium": "digital"},
        })
        for viewer in ("viewer-a", "viewer-b"):
            result = page(db, identity=viewer, page_size=1)
            assert result.matched_count == 1 and not result.has_more
            row, = result.results
            assert row.record_id == "durable"
            assert row.published_at == "2020"
            assert page(db, identity=viewer, page_size=1, page=1).status == "page_out_of_range"
        # Independent C is still available if the old per-profile mirror
        # cannot be read; there is no reason to query it on a C hit.
        def forbidden(*args, **kwargs):
            raise AssertionError("legacy mirror should not be consulted")
        monkeypatch.setattr(db, "catalog_entries", forbidden)
        assert page(db).results[0].record_id == "durable"
    finally:
        db.close()


def test_federated_outage_uses_retained_c_not_mirror(tmp_path, monkeypatch):
    """Actual orchestration: one failed request, retained C, healthy source."""
    import time
    import httpx
    from cifereca.core.notice_c_cover_index import persist_consolidated_cover_source
    from tests.test_core_p0_6366_public_c_sqlite_source_search import persist
    from tests.test_core043_progress_cache_stats import _service, _FakeSession

    service = _service(tmp_path)
    try:
        now = int(time.time())
        source = persist(service.storage, verified_public(
            source_provider="bad", record_id="durable-only",
            title="Progressive Book", format="EPUB", published_at="2020",
        ), at=now)
        persist_consolidated_cover_source(
            service.storage, canonical_edition_id=source.observation.key.edition_id,
            source=source, relation_proof_id="core.exact.observed.v1:proof",
        )
        _drop_short_source(service.storage, source)
        assert service.storage.catalog_entries() == []
        bad, good = _FakeSession("bad"), _FakeSession("good")
        attempts, mirrors, batches = [], [], []

        def fail(*args, **kwargs):
            attempts.append("one request")
            raise httpx.ConnectTimeout("PRIVATE_DIAGNOSTIC")

        bad.search = fail
        service._sessions = {"bad": bad, "good": good}
        monkeypatch.setattr(service, "_mirror_remote_results",
                            lambda rows, **kwargs: mirrors.extend(rows) or len(rows))
        summary = service.search_progressive(
            "Progressive Book", force=True, field="title", on_batch=batches.append,
        )
        assert attempts == ["one request"]
        assert summary.providers_failed == 1 and summary.result_count == 2
        stale = next(batch for batch in batches if batch.provider_id == "bad")
        live = next(batch for batch in batches if batch.provider_id == "good")
        row, = stale.results
        assert stale.result_status == "known_catalog_stale"
        assert stale.error_code == "SearchTemporarilyUnavailable"
        assert len(live.results) == 1 and live.error_code == ""
        assert live.result_status == "live"
        assert row.record_id == "durable-only" and row.published_at == "2020"
        assert row.metadata["search_provenance"] == "known_catalog_stale"
        assert row.metadata["mirrored_at"] == now
        assert not row.owned and not row.downloadable
        assert row.download_url is row.thumbnail_url is row.friendly_url is None
        assert {r.source_provider for r in mirrors} == {"good"}
        assert "PRIVATE_DIAGNOSTIC" not in repr((summary.warnings, stale.warnings))

        attempts.clear()
        mirrors.clear()
        rows = service.search("Progressive Book", force=True, field="title")
        assert attempts == ["one request"]
        assert {r.source_provider for r in rows} == {"good", "bad"}
        retained = next(r for r in rows if r.source_provider == "bad")
        assert retained.record_id == "durable-only" and not retained.owned
        assert retained.metadata["search_provenance"] == "known_catalog_stale"
        assert {r.source_provider for r in mirrors} == {"good"}

        # A real empty response is never replaced by last known C.
        attempts.clear()
        def empty(*args, **kwargs):
            attempts.append("legitimate zero")
            return []
        bad.search = empty
        rows = service.search("Progressive Book", force=True, field="title")
        assert attempts == ["legitimate zero"]
        assert {r.source_provider for r in rows} == {"good"}
        assert service.storage.entitlements() == [] and service.storage.acquisitions() == []
    finally:
        service.close()
