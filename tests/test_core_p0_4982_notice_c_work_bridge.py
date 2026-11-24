"""Real Core broker/admission + SQLite journal; synthetic publication port."""
from threading import Event

import pytest

from cifereca.core import notice_c_job_journal as journal
from cifereca.core.notice_c_work_bridge import (
    AggregateWorkBridge, PreparedAggregate,
)
from cifereca.plugin_api import Cancelled
from cifereca.storage import Storage
from cifereca.task_admission import SharedTaskAdmission
from cifereca.task_queue import TaskPriority
from cifereca.task_runtime import TaskBrokerImpl
from tests.test_core_p0_4982_notice_c_job_journal import enqueue, spec


def aggregate(value="A"):
    return spec("recompute_aggregate", value=value)


def publish(db):
    db.execute("INSERT INTO metadata(key,value) VALUES(?,?)",
               ("synthetic-aggregate-publication", "ok"))
    return True


def test_refuse_broker_without_shared_user_reserve(tmp_path):
    storage = Storage(tmp_path / "work.sqlite")
    brokers = [TaskBrokerImpl(object()), TaskBrokerImpl(
        object(), shared_admission=SharedTaskAdmission(
            max_inflight=2, reserve_user_slots=0))]
    try:
        for broker in brokers:
            with pytest.raises(ValueError, match="shared_admission_required"):
                AggregateWorkBridge(storage, broker, lambda *_: None)
    finally:
        for broker in brokers:
            broker.close()
        storage.close()


def test_backpressure_no_claim_and_real_atomic_publication(tmp_path):
    storage = Storage(tmp_path / "work.sqlite")
    budget = SharedTaskAdmission(max_inflight=2, reserve_user_slots=1)
    broker = TaskBrokerImpl(object(), max_workers=1, shared_admission=budget)
    bridge = AggregateWorkBridge(
        storage, broker, lambda *_: PreparedAggregate(publish), clock=lambda: 1)
    reserved = budget.acquire(priority=int(TaskPriority.METADATA))
    try:
        ref = enqueue(storage, aggregate())
        assert bridge.submit(ref) is None
        assert storage.metadata_get("synthetic-aggregate-publication") is None
        assert journal.read_job(storage, ref).attempts == 0
        user = budget.acquire(priority=int(TaskPriority.USER_ACTION))
        assert user is not None
        user.release()
        reserved.release()
        handle = bridge.submit(ref)
        assert handle is not None
        assert handle.result(timeout=5) == "current"
        assert journal.read_job(storage, ref).state == "current"
        assert storage.metadata_get("synthetic-aggregate-publication") == "ok"
        assert storage.entitlements() == [] and storage.acquisitions() == []
    finally:
        reserved.release()
        broker.close()
        storage.close()


def test_queue_cancellation_and_local_bound_do_not_claim(tmp_path):
    storage = Storage(tmp_path / "work.sqlite")
    budget = SharedTaskAdmission(max_inflight=5, reserve_user_slots=1)
    broker = TaskBrokerImpl(object(), max_workers=1, shared_admission=budget)
    started, release = Event(), Event()
    prepared = []
    def blocker(_control):
        started.set()
        assert release.wait(5)
    bridge = AggregateWorkBridge(
        storage, broker, lambda *_: prepared.append(1), clock=lambda: 1)
    try:
        foreground = broker.submit(blocker, priority=TaskPriority.USER_ACTION)
        assert started.wait(5)
        first = enqueue(storage, aggregate())
        second = enqueue(storage, aggregate("B"), first.generation)
        third = enqueue(storage, aggregate("C"), second.generation)
        h1 = bridge.submit(first)
        h2 = bridge.submit(second)
        assert h1 is not None and h2 is not None
        assert bridge.offer(first).outcome == "duplicate"
        assert bridge.offer(third).outcome == "blocked"
        assert bridge.submit(first) is None and bridge.submit(third) is None
        assert journal.read_job(storage, third).attempts == 0
        h1.cancel()
        h2.cancel()
        for handle in (h1, h2):
            with pytest.raises(Cancelled):
                handle.result(timeout=5)
        assert prepared == []
        assert journal.read_job(storage, third).attempts == 0
        release.set()
        foreground.result(timeout=5)
    finally:
        release.set()
        broker.close()
        storage.close()


def test_worker_rechecks_kind_generation_and_publish_rollback(tmp_path):
    storage = Storage(tmp_path / "work.sqlite")
    broker = TaskBrokerImpl(object(), max_workers=1, shared_admission=
                            SharedTaskAdmission(max_inflight=4, reserve_user_slots=1))
    now = [1]
    observed = []
    def prepare(job_spec, _control):
        observed.append(job_spec)
        def refused(db):
            publish(db)
            return False
        return PreparedAggregate(refused)
    bridge = AggregateWorkBridge(storage, broker, prepare, clock=lambda: now[0])
    try:
        source = enqueue(storage, spec("fetch_source_notice"))
        assert bridge.submit(source).result(timeout=5) == "unsupported"
        assert journal.read_job(storage, source).attempts == 0
        a = enqueue(storage, aggregate())
        b = enqueue(storage, aggregate("B"), a.generation)
        assert bridge.submit(a).result(timeout=5) == "obsolete"
        assert observed == []
        assert bridge.submit(b).result(timeout=5) == "transient_failure"
        assert storage.metadata_get("synthetic-aggregate-publication") is None
        failed = journal.read_job(storage, b)
        assert (failed.state, failed.attempts, failed.due) == ("pending", 1, 2)
        assert observed == [aggregate("B")]
    finally:
        broker.close()
        storage.close()


def test_generation_replaced_during_preparation_vetoes_writer(tmp_path):
    storage = Storage(tmp_path / "work.sqlite")
    broker = TaskBrokerImpl(object(), max_workers=1, shared_admission=
                            SharedTaskAdmission(max_inflight=3, reserve_user_slots=1))
    a = enqueue(storage, aggregate())
    called = []
    newer = []
    def prepare(_spec, _control):
        newer.append(enqueue(storage, aggregate("B"), a.generation))
        def must_not_publish(db):
            called.append(True)
            return publish(db)
        return PreparedAggregate(must_not_publish)
    bridge = AggregateWorkBridge(storage, broker, prepare, clock=lambda: 1)
    try:
        assert bridge.submit(a).result(timeout=5) == "obsolete"
        assert called == []
        assert journal.read_job(storage, newer[0]).attempts == 0
        assert storage.metadata_get("synthetic-aggregate-publication") is None
    finally:
        broker.close()
        storage.close()


def test_expiry_during_publication_rolls_back_and_preserves_reclaim(tmp_path):
    storage = Storage(tmp_path / "work.sqlite")
    broker = TaskBrokerImpl(object(), max_workers=1, shared_admission=
                            SharedTaskAdmission(max_inflight=3, reserve_user_slots=1))
    now = [1]
    def late_publish(db):
        publish(db)
        now[0] = 301
        return True
    bridge = AggregateWorkBridge(
        storage, broker, lambda *_: PreparedAggregate(late_publish),
        clock=lambda: now[0])
    try:
        ref = enqueue(storage, aggregate())
        assert bridge.submit(ref).result(timeout=5) == "obsolete"
        assert storage.metadata_get("synthetic-aggregate-publication") is None
        assert journal.read_job(storage, ref).state == "pending"
        reclaimed = journal.claim_job(storage, ref, now=301, lease_seconds=10)
        assert reclaimed.fence == 2
    finally:
        broker.close()
        storage.close()



def test_exhausted_retry_returns_terminal_receipt_not_retryable(tmp_path):
    from time import monotonic
    storage = Storage(tmp_path / "exhausted.sqlite")
    budget = SharedTaskAdmission(max_inflight=4, reserve_user_slots=1)
    broker = TaskBrokerImpl(object(), max_workers=1, shared_admission=budget)
    now = [1]
    calls = []

    def prepare(_spec, _control):
        calls.append(now[0])
        def refused(db):
            publish(db)
            return False
        return PreparedAggregate(refused)

    bridge = AggregateWorkBridge(storage, broker, prepare, clock=lambda: now[0])

    def submit():
        # Completion notification may precede release of broker admission
        # and the bridge's exact-reference dedup entry.
        deadline = monotonic() + 5
        while monotonic() < deadline:
            handle = bridge.submit(ref)
            if handle is not None:
                return handle.result(timeout=5)
            Event().wait(.005)
        pytest.fail("broker did not release completed job")

    try:
        ref = enqueue(storage, aggregate())
        for attempt, due in ((1, 2), (2, 6)):
            assert submit() == "transient_failure"
            receipt = journal.read_job(storage, ref)
            assert (receipt.state, receipt.attempts, receipt.due, receipt.reason) == (
                "pending", attempt, due, "transient",
            )
            assert storage.metadata_get("synthetic-aggregate-publication") is None
            assert submit() == "not_ready"
            now[0] = due
        assert submit() == "error"
        exhausted = journal.read_job(storage, ref)
        assert (exhausted.state, exhausted.attempts, exhausted.reason, exhausted.lease_until) == (
            "error", 3, "exhausted", 0,
        )
        now[0] += 100
        assert submit() == "not_ready"
        assert journal.read_job(storage, ref) == exhausted
        assert calls == [1, 2, 6]
        assert storage.metadata_get("synthetic-aggregate-publication") is None
        assert storage.entitlements() == [] and storage.acquisitions() == []
    finally:
        broker.close()
        storage.close()


def test_network_circuit_cannot_consume_both_documentary_slots(tmp_path):
    storage = Storage(tmp_path / "circuits.sqlite")
    budget = SharedTaskAdmission(max_inflight=5, reserve_user_slots=1)
    broker = TaskBrokerImpl(object(), max_workers=1, shared_admission=budget)
    entered, release = Event(), Event()

    def blocker(_control):
        entered.set()
        assert release.wait(10)

    bridge = AggregateWorkBridge(storage, broker, lambda *_: PreparedAggregate(publish),
                                 clock=lambda: 1)
    try:
        foreground = broker.submit(blocker, priority=TaskPriority.USER_ACTION)
        assert entered.wait(10)
        first = enqueue(storage, spec("fetch_source_notice"))
        second = enqueue(storage, spec("fetch_source_notice", value="B"), first.generation)
        local = enqueue(storage, aggregate())
        h1 = bridge.offer(first, circuit="network")
        assert h1.outcome == "accepted"
        assert bridge.offer(second, circuit="network").outcome == "circuit_blocked"
        h2 = bridge.offer(local, circuit="local")
        assert h2.outcome == "accepted"
        assert journal.read_job(storage, second).attempts == 0
        h1.handle.cancel()
        h2.handle.cancel()
        for handle in (h1.handle, h2.handle):
            with pytest.raises(Cancelled):
                handle.result(timeout=5)
        release.set()
        foreground.result(timeout=5)
    finally:
        release.set()
        broker.close()
        storage.close()
