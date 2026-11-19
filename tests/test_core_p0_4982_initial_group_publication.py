"""#4982 first public OriginLink group recovery, with actual SQLite publication graph."""
from threading import Event

from cifereca.core import notice_c_job_journal as journal
from cifereca.core import notice_c_cover_index as index
from cifereca.core import notice_c_initial_group_publication as initial
from cifereca.core import notice_c_origin_link_producer as origin
from cifereca.core import search_orchestration as search
from cifereca.core.notice_cover_repository import evict_notice_cover
from cifereca.task_admission import SharedTaskAdmission
from tests.test_core_p0_4982_notice_work_runtime import open_core
from tests.test_core_p0_4982_search_repair import _admitted
from tests.test_core_p0_6366_library_recent_public_c import _real_origins, _source, A_URL, B_URL


def _budget():
    return SharedTaskAdmission(max_inflight=4, reserve_user_slots=1)


def _seed(core, tmp_path):
    _real_origins(core.storage, tmp_path)
    sources = (_source(core.storage, "seller-a", "edition-a", A_URL),
               _source(core.storage, "seller-b", "edition-b", B_URL))
    for source in sources:
        index.persist_exact_public_search_cover(core.storage, source)
    return sources


def test_initial_group_dedup_restart_and_atomic_commit(tmp_path):
    with open_core(tmp_path, notice_work_admission=_budget()) as core:
        sources = _seed(core, tmp_path)
        ref = initial.request_initial_group_publication(core.storage, sources[0], now=100)
        assert ref is not None
        assert initial.request_initial_group_publication(core.storage, sources[1], now=101) == ref
        job = journal.read_job(core.storage, ref)
        assert job.spec.source_ref == min(s.document.source_key for s in sources)
        assert job.spec.kind == "relink_notices" and job.attempts == 0
        assert core.brokers["tasks"]._executor is None
    with open_core(tmp_path, notice_work_admission=_budget()) as core:
        events = []
        def observe(document, epoch):
            events.append((document, epoch, journal.read_job(core.storage, ref).state))
        core.notice_work.bind_committed_observer(observe)
        worker, = core.notice_work.request_tick().result(timeout=10).handles
        assert worker.result(timeout=10) == "current"
        (document, epoch, state), = events
        assert document.source_key == job.spec.target and state == "current"
        c = index.read_consolidated_notice_cover(core.storage, document)
        assert c.epoch == epoch and len(c.sources) == 2
        assert {m.source_ref for m in c.sources} == {s.document.source_key for s in sources}
        assert initial.request_initial_group_publication(core.storage, sources[0], now=102) is None
        assert core.storage.entitlements() == [] and core.storage.acquisitions() == []


def test_missing_group_live_failure_is_recovered(tmp_path, monkeypatch):
    with open_core(tmp_path, notice_work_admission=_budget()) as core:
        _real_origins(core.storage, tmp_path)
        rows = [_admitted("Même titre", "edition-a", source_provider="seller-a", thumbnail_url=A_URL),
                _admitted("Même titre", "edition-b", source_provider="seller-b", thumbnail_url=B_URL)]
        done = Event()
        events = []
        def observe(document, epoch):
            events.append((document, epoch))
            done.set()
        core.notice_work.bind_committed_observer(observe)
        def fail(*args, **kwargs):
            raise OSError("simulated initial group writer failure")
        with monkeypatch.context() as patch:
            patch.setattr(origin, "persist_origin_linked_public_cover_group", fail)
            pairs = search._ingest_public_search_c(
                core.library, rows, identity_id="alice", now=100,
                require_public_cover_witness=True,
            )
        assert len(pairs) == 2 and all(own is not None for _, _, own in pairs)
        assert done.wait(10)
        (document, epoch), = events
        canonical = index.read_consolidated_notice_cover(core.storage, document)
        assert canonical.epoch == epoch and len(canonical.sources) == 2
        assert {m.source_ref for m in canonical.sources} == {s.document.source_key for _, s, _ in pairs}
        assert all(row._core_notice_canonical_cover_document_ref != document for row in rows)
        assert core.storage.entitlements() == [] and core.storage.acquisitions() == []


def test_competing_live_group_is_noop(tmp_path):
    with open_core(tmp_path, notice_work_admission=_budget()) as core:
        sources = _seed(core, tmp_path)
        ref = initial.request_initial_group_publication(core.storage, sources[0], now=100)
        c = origin.reconcile_origin_linked_public_c(core.storage, sources[0])
        assert c is not None and len(c.sources) == 2
        events = []
        core.notice_work.bind_committed_observer(lambda *args: events.append(args))
        worker, = core.notice_work.request_tick().result(timeout=10).handles
        assert worker.result(timeout=10) == "current"
        assert journal.read_job(core.storage, ref).state == "current"
        assert not events and index.read_consolidated_notice_cover(core.storage, c.document) == c


def test_missing_source_prevents_publication(tmp_path):
    with open_core(tmp_path, notice_work_admission=_budget()) as core:
        sources = _seed(core, tmp_path)
        ref = initial.request_initial_group_publication(core.storage, sources[0], now=100)
        assert ref is not None
        assert evict_notice_cover(core.storage, sources[1].document, expected_epoch=sources[1].epoch)
        events = []
        core.notice_work.bind_committed_observer(lambda *args: events.append(args))
        worker, = core.notice_work.request_tick().result(timeout=10).handles
        assert worker.result(timeout=10) == "transient_failure"
        assert journal.read_job(core.storage, ref).state == "pending"
        assert not events
        with core.storage._lock, core.storage._connect() as db:
            # Only the two initial exact self-C rows survive.
            assert db.execute("SELECT count(*) FROM metadata WHERE key>=? AND key<?",
                              (index._PREFIX, index._PREFIX + "\uffff")).fetchone()[0] == 2



def test_group_intent_and_source_rollback_together_then_survive_restart(tmp_path):
    from cifereca.core.notice_cover_repository import (
        persist_notice_cover_many, read_notice_cover,
    )
    from cifereca.core.notice_search_cover_producer import admit_public_search_catalogue
    from tests.test_core_p0_6366_public_c_sqlite_source_search import verified_public

    with open_core(tmp_path, notice_work_admission=_budget()) as core:
        _real_origins(core.storage, tmp_path)
        first = _source(core.storage, "seller-a", "edition-a", A_URL)
        index.persist_exact_public_search_cover(core.storage, first)
        row = verified_public(source_provider="seller-b", record_id="edition-b",
                              title="Un ouvrage commun", author="Autrice",
                              thumbnail_url=B_URL)
        observation = admit_public_search_catalogue(
            row, identity_id="alice", observed_at=101, public_cover_url=B_URL,
        )
        assert observation is not None
        attempts = []

        def request_then_fail(db, source, position):
            assert position == 0
            ref = initial.request_search_group_repair_txn(db, source, now=101)
            assert ref is not None
            attempts.append((source, ref))
            # Simulate failure after both writes, before the row savepoint commits.
            raise ValueError("simulated source transaction failure")

        def ingest(callback):
            return persist_notice_cover_many(
                core.storage, [observation],
                field_path=("search_result", "thumbnail_url"),
                cover_inventory_complete=True, _after_persist=callback,
            )

        assert ingest(request_then_fail) == [None]
        (rolled_back, _), = attempts
        assert read_notice_cover(core.storage, rolled_back.document) is None
        with core.storage._lock, core.storage._connect() as db:
            assert db.execute("SELECT count(*) FROM metadata WHERE key>=? AND key<?",
                              (journal._PREFIX, journal._END)).fetchone()[0] == 0

        def request(db, source, position):
            ref = initial.request_search_group_repair_txn(db, source, now=101)
            assert ref is not None
            attempts.append((source, ref))

        source, = ingest(request)
        assert source is not None
        _, ref = attempts[-1]
        assert journal.read_job(core.storage, ref).state == "pending"
        assert read_notice_cover(core.storage, source.document) == source
        assert core.brokers["tasks"]._executor is None

    with open_core(tmp_path, notice_work_admission=_budget()) as core:
        worker, = core.notice_work.request_tick().result(timeout=10).handles
        assert worker.result(timeout=10) == "current"
        job = journal.read_job(core.storage, ref)
        assert job.state == "current" and job.attempts == 1
        with core.storage._lock, core.storage._connect() as db:
            keys = db.execute("SELECT key FROM metadata WHERE key>=? AND key<?",
                              (index._PREFIX, index._PREFIX + "\uffff")).fetchall()
        assert len(keys) == 2  # The first self-C and the recovered group C.
        assert core.storage.entitlements() == [] and core.storage.acquisitions() == []
