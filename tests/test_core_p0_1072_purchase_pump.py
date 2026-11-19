"""Same pump, no dormant-purchase broker jobs, exact sparse-key admission."""
from contextlib import contextmanager, nullcontext

from cifereca.core import notice_c_job_journal as journal
from cifereca.core.notice_c_purchase_execution import PurchaseIntentExecution
from cifereca.core.notice_c_work_bridge import PreparedSource
from cifereca.core.notice_c_work_pump import PublicNoticeWorkPump
from cifereca.core.notice_c_work_runtime import NoticeWorkRuntime
from cifereca.core.canonical_sync_stage import persist_attested_source_references
from cifereca.storage import Storage
from cifereca.task_admission import SharedTaskAdmission
from cifereca.task_runtime import TaskBrokerImpl
from tests.test_core_p0_1072_durable_purchase_intent import prepare, set_policy, request
from tests.test_core_p0_4982_fetch_execution import broker


@contextmanager
def allowed():
    yield True


def test_single_pump_skips_dormant_intents_and_visits_sparse_live_ref(tmp_path):
    db = Storage(tmp_path / "pump.sqlite")
    tasks = broker()
    clock = [100]
    executor = PurchaseIntentExecution(db, clock=lambda: clock[0])
    try:
        snapshot, candidate = prepare(db)
        persist_attested_source_references(db, snapshot)
        set_policy(db, "true")
        ref, = request(db, snapshot, candidate)
        # Simulate a large durable pending journal; none of these unrelated
        # inert coordinates has a live qualified transport admission.
        for n in range(90):
            spec = journal.JobSpec(
                "fetch_source_notice", journal._PURCHASE_TARGET + f"{n:064x}",
                "a" * 64, journal._PURCHASE_INTENT_DIGEST,
            )
            journal.request_job(db, spec, now=100, expected_generation=0)
        calls = []
        pump = PublicNoticeWorkPump(db, tasks, clock=lambda: clock[0],
                                    purchase_execution=executor)
        assert pump.tick().handles == ()
        assert journal.read_job(db, ref).attempts == 0
        def execute(selected, guard, control):
            assert selected == candidate and guard()
            calls.append("execute")
            def publish(sql):
                calls.append("publish")
                return True
            return PreparedSource(publish, nullcontext)
        assert executor.attest(ref, snapshot, candidate, hold_context=allowed,
                               execute=execute, ttl_seconds=10)
        handle, = pump.tick().handles
        assert handle.result(timeout=10) == "current"
        assert calls == ["execute", "publish"]
        assert pump.tick().handles == ()
        assert journal.read_job(db, ref).state == "current"
        assert executor.ready_refs() == ()
    finally:
        executor.close()
        tasks.close()
        db.close()


def test_expired_live_hint_is_removed_without_attempt(tmp_path):
    db = Storage(tmp_path / "expire.sqlite")
    tasks = broker()
    clock = [100]
    executor = PurchaseIntentExecution(db, clock=lambda: clock[0])
    try:
        snapshot, candidate = prepare(db)
        persist_attested_source_references(db, snapshot)
        set_policy(db, "true")
        ref, = request(db, snapshot, candidate)
        def forbidden(*args):
            raise AssertionError("no qualified transport")
        assert executor.attest(ref, snapshot, candidate, hold_context=allowed,
                               execute=forbidden, ttl_seconds=1)
        clock[0] = 101
        pump = PublicNoticeWorkPump(db, tasks, clock=lambda: clock[0],
                                    purchase_execution=executor)
        assert pump.tick().handles == ()
        assert executor.ready_refs() == ()
        assert journal.read_job(db, ref).attempts == 0
    finally:
        executor.close()
        tasks.close()
        db.close()


def test_runtime_installs_once_before_tick_and_owns_close(tmp_path):
    db = Storage(tmp_path / "runtime.sqlite")
    tasks = TaskBrokerImpl(object(), max_workers=3, shared_admission=
                           SharedTaskAdmission(max_inflight=4, reserve_user_slots=1))
    runtime = NoticeWorkRuntime(db, tasks, clock=lambda: 100)
    executor = PurchaseIntentExecution(db, clock=lambda: 100)
    other = PurchaseIntentExecution(db, clock=lambda: 100)
    try:
        assert runtime.install_purchase_execution(executor)
        assert runtime._pump._purchase_execution is executor
        assert not runtime.install_purchase_execution(other)
        assert runtime.request_tick().result(timeout=10).handles == ()
        runtime.close()
        assert executor._closed is True and other._closed is False
        assert not runtime.install_purchase_execution(other)
    finally:
        runtime.close()
        other.close()
        tasks.close()
        db.close()
