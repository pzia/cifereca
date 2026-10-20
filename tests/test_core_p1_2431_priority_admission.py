"""#2431: deterministic admission, cancellation and shutdown without GTK."""
from __future__ import annotations

import threading

import pytest

from cifereca.task_queue import PriorityExecutor, TaskPriority


def test_optional_cap_rejects_immediately_preserving_priorities_and_counters():
    pool = PriorityExecutor(max_workers=1, max_pending=2, thread_name_prefix="test-admission")
    entered, release = threading.Event(), threading.Event()
    order = []

    def first():
        entered.set()
        assert release.wait(3)
        order.append("first")

    try:
        running = pool.submit(TaskPriority.NORMAL, first)
        assert entered.wait(2)
        low = pool.submit(TaskPriority.MAINTENANCE, lambda: order.append("background"))
        high = pool.submit(TaskPriority.USER_ACTION, lambda: order.append("user"))
        assert pool.admission_snapshot() == {
            "pending": 2, "active": 1, "admitted": 3, "rejected": 0,
            "max_pending": 2, "closed": False,
        }
        with pytest.raises(RuntimeError, match="executor pending queue full"):
            pool.submit(TaskPriority.NORMAL, lambda: pytest.fail("rejected work ran"))
        assert pool.admission_snapshot()["rejected"] == 1
        release.set()
        running.result(timeout=3)
        high.result(timeout=3)
        low.result(timeout=3)
        pool.shutdown(wait=True)
        assert order == ["first", "user", "background"]
        assert pool.admission_snapshot()["pending"] == 0
        assert pool.admission_snapshot()["active"] == 0
    finally:
        release.set()
        pool.shutdown(wait=True)


def test_cancelled_pending_task_never_runs_and_does_not_strand_shutdown():
    pool = PriorityExecutor(max_workers=1, max_pending=1, thread_name_prefix="test-cancel-admission")
    entered, release = threading.Event(), threading.Event()
    fired = []
    try:
        first = pool.submit(20, lambda: (entered.set(), release.wait(3)))
        assert entered.wait(2)
        cancelled = pool.submit(40, lambda: fired.append("unexpected"))
        assert cancelled.cancel()
        # A cancelled item still occupies a physical queue slot until dequeued.
        # This bounds memory even under repeated cancellation/resubmission.
        with pytest.raises(RuntimeError, match="queue full"):
            pool.submit(40, lambda: fired.append("unexpected"))
        release.set()
        first.result(timeout=3)
        pool.shutdown(wait=True)
        assert cancelled.cancelled()
        assert pool.admission_snapshot()["pending"] == 0
        assert fired == []
    finally:
        release.set()
        pool.shutdown(wait=True)


def test_shutdown_cancellation_callback_can_observe_closed_metrics_without_deadlock():
    pool = PriorityExecutor(max_workers=1, max_pending=1, thread_name_prefix="test-callback-admission")
    entered, release = threading.Event(), threading.Event()
    try:
        first = pool.submit(20, lambda: (entered.set(), release.wait(3)))
        assert entered.wait(2)
        pending = pool.submit(40, lambda: pytest.fail("cancelled work ran"))
        snapshots = []
        pending.add_done_callback(lambda _: snapshots.append(pool.admission_snapshot()))
        pool.shutdown(wait=False, cancel_futures=True)
        assert pending.cancelled()
        assert len(snapshots) == 1
        assert snapshots[0]["closed"] is True
        assert snapshots[0]["pending"] == 0
        with pytest.raises(RuntimeError, match="executor already closed"):
            pool.submit(0, lambda: None)
        release.set()
        first.result(timeout=3)
    finally:
        release.set()
        pool.shutdown(wait=True)


@pytest.mark.parametrize("invalid", [0, -1, True, 2.0, "2"])
def test_invalid_pending_cap_is_rejected_before_threads_start(invalid):
    with pytest.raises(ValueError, match="max_pending"):
        PriorityExecutor(max_pending=invalid)


def test_default_stays_unbounded_and_snapshot_never_contains_task_data():
    pool = PriorityExecutor(max_workers=1, thread_name_prefix="test-legacy-admission")
    entered, release = threading.Event(), threading.Event()
    try:
        first = pool.submit(30, lambda: (entered.set(), release.wait(3)))
        assert entered.wait(2)
        tasks = [pool.submit(40, lambda: None) for _ in range(5)]
        metrics = pool.admission_snapshot()
        assert metrics["max_pending"] is None
        assert metrics["pending"] == 5
        assert set(metrics) == {
            "pending", "active", "admitted", "rejected", "max_pending", "closed",
        }
        assert "PRIVATE_TOKEN" not in repr(metrics)
        release.set()
        first.result(timeout=3)
        for task in tasks:
            task.result(timeout=3)
    finally:
        release.set()
        pool.shutdown(wait=True)
