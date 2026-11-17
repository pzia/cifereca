"""#6366: cache HIT after restart must still resolve the same persisted Notice C.

The search-result cache stores no per-viewer image association or writer
stamp. On replay Core verifies the existing canonical C and its exact public
bibliographic source, even after the shorter-lived provider source is purged.
"""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace as NS

from cifereca.core import search_orchestration
from cifereca.plugin_api import ProviderSearchResponse, PublicSearchNotice
from cifereca.core.notice_cover_repository import read_notice_cover
from cifereca.gui.notice_cover_cached_reader import cached_notice_cover_snapshot
from cifereca.models import SearchResult
from tests.test_core043_progress_cache_stats import _FakeSession, _service


URL = "https://covers.example.org/cache-replay.jpg"


class _Public(_FakeSession):
    def __init__(self):
        super().__init__("public-catalogue")
        self.credential_id = "__public__"
        self.credentials = {}
        self._accounts = []
        self.calls = 0

    def search(self, query, scope, *, field="all", content_types=()):
        self.calls += 1
        source = SearchResult(
            id="public:book:1", record_id="public:book:1",
            docbase="PUBLIC", medium="digital", title="Progressive Book",
            author="A", type="ebook", format="EPUB",
            publisher=None, published_at=None, description=None,
            thumbnail_url=URL, friendly_url=None,
            scenario="PUBLIC", source_provider=self.provider_id,
            source_credential="__public__", source_collection="public_catalog",
            owned=False, downloadable=False, download_url=None,
        )
        # Produce a real same-call public catalogue projection in Core,
        # never publish a legacy list-only row by its public-looking fields.
        return search_orchestration._public_rows_from_response(
            ProviderSearchResponse(
                results=[source], public_notices=(PublicSearchNotice(source),),
            ), self,
        )


def test_search_cache_sync_and_progressive_replay_after_process_restart(tmp_path):
    service = _service(tmp_path)
    original = _Public()
    service._sessions = {original.provider_id: original}
    try:
        live = service.search("Progressive Book", provider_id=original.provider_id,
                              force=True)
        assert len(live) == 1 and original.calls == 1
        ref = live[0]._core_notice_cover_document_ref
        canonical = live[0]._core_notice_canonical_cover_document_ref
        assert canonical is not None and canonical != ref
        assert ref.identity_id is None and ref.kind == "public_catalogue"
        persisted = read_notice_cover(service.storage, ref)
        assert persisted is not None and persisted.epoch == 1

        cached = service.search("Progressive Book", provider_id=original.provider_id,
                                force=False)
        assert len(cached) == 1 and cached[0] is not live[0]
        assert cached[0]._core_notice_cover_document_ref == ref
        assert cached[0]._core_notice_canonical_cover_document_ref == canonical
        assert original.calls == 1
        assert read_notice_cover(service.storage, ref).epoch == 1
        # Sync and progressive cache keys are distinct today. Warm the
        # progressive producer's own slot before restarting; this P0 change
        # restores the *same Notice C association* in each already-existing
        # cache, without claiming the two search HTTP caches are merged.
        produced = service.search_progressive(
            "Progressive Book", provider_id=original.provider_id, force=True,
        )
        assert produced.result_count == 1
        assert original.calls == 2
        assert service.last_search[0]._core_notice_cover_document_ref == ref
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
        assert rows[0]._core_notice_canonical_cover_document_ref == canonical
        from cifereca.gui.search_document_cover_bridge import exact_remote_search_document
        from tests.test_core_p1_6168_search_remote_document_gateway import host
        # The actual Search list/detail bridge must consume this cached Core
        # stamp, not create another search-exact N2 source after restart.
        window, _, row_key = host(rows[0])
        packet = exact_remote_search_document(window, rows[0], row_key)
        assert packet is not None and packet.document == canonical
        batches = []
        result = reopened.search_progressive(
            "Progressive Book", provider_id=new_session.provider_id,
            force=False, on_batch=batches.append,
        )
        assert not result.cancelled and len(batches) == 1
        assert batches[0].cached is True
        assert batches[0].results[0]._core_notice_cover_document_ref == ref
        assert batches[0].results[0]._core_notice_canonical_cover_document_ref == canonical
        assert new_session.calls == 0
        assert read_notice_cover(reopened.storage, ref).epoch == 1
        assert cached_notice_cover_snapshot(
            NS(storage=reopened.storage), ref,
        ).candidates[0].url == URL
        assert cached_notice_cover_snapshot(
            NS(storage=reopened.storage), canonical,
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


def test_cache_replay_uses_c_after_source_ttl_but_rejects_missing_c(
    tmp_path,
):
    service = _service(tmp_path)
    session = _Public()
    service._sessions = {session.provider_id: session}
    try:
        live = service.search("Progressive Book", provider_id=session.provider_id,
                              force=True)
        ref = live[0]._core_notice_cover_document_ref
        canonical = live[0]._core_notice_canonical_cover_document_ref
        with service.storage._lock, service.storage._connect() as db:
            db.execute("DELETE FROM metadata WHERE key LIKE 'notice.c.observation.v1:%'")
        cached = service.search("Progressive Book", provider_id=session.provider_id,
                                force=False)
        assert cached[0]._core_notice_cover_document_ref == ref
        assert cached[0]._core_notice_canonical_cover_document_ref == canonical
        assert read_notice_cover(service.storage, ref) is None
        assert cached_notice_cover_snapshot(NS(storage=service.storage), ref) is None
        assert cached_notice_cover_snapshot(
            NS(storage=service.storage), canonical,
        ).candidates[0].url == URL
        from cifereca.gui.search_document_cover_bridge import exact_remote_search_document
        from tests.test_core_p1_6168_search_remote_document_gateway import host
        window, _, key = host(cached[0])
        assert exact_remote_search_document(window, cached[0], key).document == canonical

        # Exact same source ID with forged cached title is not its edition.
        altered = replace(cached[0], title="Different edition")
        search_orchestration._stamp_cached_public_notice_refs(
            [altered], identity_id="alice", storage=service.storage,
        )
        assert altered._core_notice_cover_document_ref == ref
        assert not hasattr(altered, "_core_notice_canonical_cover_document_ref")

        # No C + no source cannot confer an image from the physical N3 cache.
        with service.storage._lock, service.storage._connect() as db:
            db.execute("DELETE FROM metadata WHERE key LIKE 'notice.c.cover.canonical.v1:%'")
        without_c = service.search(
            "Progressive Book", provider_id=session.provider_id, force=False,
        )
        assert without_c[0]._core_notice_cover_document_ref == ref
        assert not hasattr(without_c[0], "_core_notice_canonical_cover_document_ref")
        assert cached_notice_cover_snapshot(
            NS(storage=service.storage), canonical,
        ) is None
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
        {"source_provider": "invalid provider id"},
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

def test_cached_recommendation_binding_requires_original_worker_receipt(tmp_path):
    from cifereca.gui.plugin_public_c_binding import (
        issue_public_c_binding, public_c_binding_revision,
    )
    service = _service(tmp_path)
    session = _Public()
    service._sessions = {session.provider_id: session}
    try:
        live = service.search("Progressive Book", provider_id=session.provider_id,
                              force=True)[0]
        ref = live._core_notice_canonical_cover_document_ref
        with service.storage._lock, service.storage._connect() as db:
            db.execute("DELETE FROM metadata WHERE key LIKE 'notice.c.observation.v1:%'")
        cached = service.search("Progressive Book", provider_id=session.provider_id,
                                force=False)[0]
        from cifereca.core.notice_c_cached_revision import matching_cached_public_canonical_ref
        # Bind under an explicitly captured viewer, independent of fixture defaults.
        search_orchestration._stamp_cached_public_notice_refs(
            [cached], identity_id="alice", storage=service.storage,
        )
        assert matching_cached_public_canonical_ref(cached, identity_id="alice") == ref
        window = NS(service=NS(current_identity=NS(identity_id="alice"),
                               _account_context_epoch=1, _source_context_epoch=2))
        document, receipt = issue_public_c_binding(window, cached, identity_id="alice")
        assert document == ref
        assert public_c_binding_revision(window, ref, receipt) == 1
        assert issue_public_c_binding(window, replace(cached), identity_id="alice") is None
        cached.title = "Wrong edition"
        assert public_c_binding_revision(window, ref, receipt) is None
        cached.title = live.title
        window.service._account_context_epoch += 1
        assert public_c_binding_revision(window, ref, receipt) is None
        window.service.current_identity.identity_id = "bob"
        assert issue_public_c_binding(window, cached, identity_id="bob") is None
        assert session.calls == 1
    finally:
        service.close()


def test_cached_multisource_batch_shares_exact_c_after_sqlite_restart(tmp_path, monkeypatch):
    from cifereca.core.notice_c_cached_revision import matching_cached_public_canonical_ref
    from cifereca.gui.search_document_cover_bridge import exact_remote_search_document
    from cifereca.storage import Storage
    from tests.test_core_p0_6366_origin_attested_public_c import _origins, _source_rows, _mirror
    from tests.test_core_p1_6168_search_remote_document_gateway import host

    filename = tmp_path / "cached-multisource.sqlite"
    db = Storage(filename)
    try:
        _origins(db, tmp_path)
        a, b = _source_rows()
        assert _mirror(db, a) == 1 and _mirror(db, b) == 1
        shared = b._core_notice_canonical_cover_document_ref
        with db._lock, db._connect() as sql:
            sql.execute("DELETE FROM metadata WHERE key LIKE 'notice.c.observation.v1:%'")
    finally:
        db.close()

    db = Storage(filename)
    try:
        rows = [replace(a), replace(b)]
        connect = db._connect
        calls = []
        def counted_connect():
            calls.append(1)
            return connect()
        monkeypatch.setattr(db, "_connect", counted_connect)
        search_orchestration._stamp_cached_public_notice_refs(
            rows, identity_id="alice", storage=db,
        )
        assert calls == [1]
        for row in rows:
            assert matching_cached_public_canonical_ref(row, identity_id="alice") == shared
            window, _, key = host(row)
            assert exact_remote_search_document(window, row, key).document == shared
            assert matching_cached_public_canonical_ref(replace(row), identity_id="alice") is None
            assert matching_cached_public_canonical_ref(row, identity_id="bob") is None
    finally:
        db.close()
