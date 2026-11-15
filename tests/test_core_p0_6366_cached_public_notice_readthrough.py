"""#6366: cache HIT after restart must still resolve the same persisted Notice C.

The search-result cache stores no per-viewer image association or writer
stamp. Replaying it produces an opaque source coordinate only: the N2 worker
still requires a durable, exact source observation for the image.
"""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace as NS

from cifereca.core import search_orchestration
from cifereca.core.notice_cover_repository import read_notice_cover
from cifereca.gui.notice_cover_cached_reader import cached_notice_cover_snapshot
from cifereca.models import SearchResult
from tests.test_core043_progress_cache_stats import _FakeSession, _service


URL = "https://covers.example.org/cache-replay.jpg"


class _Public(_FakeSession):
    def __init__(self):
        super().__init__("public-catalogue")
        self.credential_id = "__public__"
        self.calls = 0

    def search(self, query, scope, *, field="all", content_types=()):
        self.calls += 1
        return [SearchResult(
            id="public:book:1", record_id="public:book:1",
            docbase="PUBLIC", medium="digital", title="Progressive Book",
            author="A", type="ebook", format="EPUB",
            publisher=None, published_at=None, description=None,
            thumbnail_url=URL, friendly_url=None,
            scenario="PUBLIC", source_provider=self.provider_id,
            source_credential="__public__", source_collection="public_catalog",
            owned=False, downloadable=False, download_url=None,
        )]


def test_search_cache_sync_and_progressive_replay_after_process_restart(tmp_path):
    service = _service(tmp_path)
    original = _Public()
    service._sessions = {original.provider_id: original}
    try:
        live = service.search("Progressive Book", provider_id=original.provider_id,
                              force=True)
        assert len(live) == 1 and original.calls == 1
        ref = live[0]._core_notice_cover_document_ref
        assert ref.identity_id is None and ref.kind == "public_catalogue"
        persisted = read_notice_cover(service.storage, ref)
        assert persisted is not None and persisted.epoch == 1

        cached = service.search("Progressive Book", provider_id=original.provider_id,
                                force=False)
        assert len(cached) == 1 and cached[0] is not live[0]
        assert cached[0]._core_notice_cover_document_ref == ref
        assert original.calls == 1
        assert read_notice_cover(service.storage, ref).epoch == 1
    finally:
        service.close()

    # Search cache and Notice C survive process teardown. The latter is the
    # sole documentary authority; the source ref never keys by viewer.
    reopened = _service(tmp_path)
    new_session = _Public()
    reopened._sessions = {new_session.provider_id: new_session}
    try:
        rows = reopened.search("Progressive Book", provider_id=new_session.provider_id,
                               force=False)
        assert len(rows) == 1
        assert new_session.calls == 0  # No supplier fetch or reparsing.
        assert rows[0]._core_notice_cover_document_ref == ref
        batches = []
        result = reopened.search_progressive(
            "Progressive Book", provider_id=new_session.provider_id,
            force=False, on_batch=batches.append,
        )
        assert not result.cancelled and len(batches) == 1
        assert batches[0].cached is True
        assert batches[0].results[0]._core_notice_cover_document_ref == ref
        assert new_session.calls == 0
        assert read_notice_cover(reopened.storage, ref).epoch == 1
        assert cached_notice_cover_snapshot(
            NS(storage=reopened.storage), ref,
        ).candidates[0].url == URL

        # A→B→A reuses the source coordinate and physical image cache; no
        # per-viewer copy is created in Core or in the row cache.
        search_orchestration._stamp_cached_public_notice_refs(
            list(batches[0].results), identity_id="bob",
        )
        assert batches[0].results[0]._core_notice_cover_document_ref == ref
        assert read_notice_cover(reopened.storage, ref).epoch == 1
    finally:
        reopened.close()


def test_cache_row_does_not_invent_persisted_cover_if_notice_source_missing(
    tmp_path,
):
    service = _service(tmp_path)
    session = _Public()
    service._sessions = {session.provider_id: session}
    try:
        live = service.search("Progressive Book", provider_id=session.provider_id,
                              force=True)
        ref = live[0]._core_notice_cover_document_ref
        with service.storage._lock, service.storage._connect() as db:
            db.execute("DELETE FROM metadata WHERE key LIKE 'notice.c.observation.v1:%'")
        cached = service.search("Progressive Book", provider_id=session.provider_id,
                                force=False)
        assert cached[0]._core_notice_cover_document_ref == ref
        # A coordinate is NOT a proof. N2 can only select from an actually
        # persisted source; a warm N3 image cannot authorize it.
        assert read_notice_cover(service.storage, ref) is None
        assert cached_notice_cover_snapshot(NS(storage=service.storage), ref) is None
        assert session.calls == 1
    finally:
        service.close()


def test_cached_public_coordinate_replay_rejects_stale_private_and_signed_rows():
    from tests.test_core_p0_6366_real_search_notice_c_ingestion import public_row
    clean = public_row()
    for mutation in (
        {"source_credential": "private-session"},
        {"source_collection": "library"},
        {"source_account_id": "account"},
        {"owned": True},
        {"downloadable": True},
        {"thumbnail_url": URL + "?token=private"},
        {"metadata": {"search_provenance": "known_catalog_stale"}},
    ):
        row = replace(clean, **mutation)
        search_orchestration._stamp_cached_public_notice_refs(
            [row], identity_id="alice",
        )
        assert not hasattr(row, "_core_notice_cover_document_ref")
    search_orchestration._stamp_cached_public_notice_refs(
        [clean], identity_id=None,
    )
    assert not hasattr(clean, "_core_notice_cover_document_ref")
