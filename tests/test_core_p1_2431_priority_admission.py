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


def test_shutdown_from_worker_does_not_join_self_or_other_worker():
    """Worker-initiated close is nonblocking even when wait=True was asked."""
    pool = PriorityExecutor(max_workers=2, max_pending=1, thread_name_prefix="test-worker-stop")
    first_started, other_started, other_release = (threading.Event() for _ in range(3))

    def other():
        other_started.set()
        assert other_release.wait(3)
        return "other"

    def initiator():
        first_started.set()
        assert other_started.wait(2)
        pool.shutdown(wait=True, cancel_futures=True)
        # A join of the other worker would block before reaching this line.
        other_release.set()
        return "closed-from-worker"

    try:
        first = pool.submit(TaskPriority.USER_ACTION, initiator)
        assert first_started.wait(2)
        second = pool.submit(TaskPriority.NORMAL, other)
        assert first.result(timeout=3) == "closed-from-worker"
        assert second.result(timeout=3) == "other"
        assert pool.admission_snapshot()["closed"] is True
        with pytest.raises(RuntimeError, match="executor already closed"):
            pool.submit(TaskPriority.USER_ACTION, lambda: None)
        # A caller outside the executor can join those worker threads.
        for thread in pool._threads:
            thread.join(timeout=3)
        assert all(not thread.is_alive() for thread in pool._threads)
    finally:
        other_release.set()
        for thread in pool._threads:
            thread.join(timeout=3)


def test_worker_shutdown_cancels_queued_work_before_return():
    pool = PriorityExecutor(max_workers=1, max_pending=1, thread_name_prefix="test-worker-cancel")
    entered, proceed = threading.Event(), threading.Event()
    try:
        def first():
            entered.set()
            assert proceed.wait(3)
            pool.shutdown(wait=True, cancel_futures=True)
            return pool.admission_snapshot()

        running = pool.submit(TaskPriority.NORMAL, first)
        assert entered.wait(2)
        waiting = pool.submit(TaskPriority.MAINTENANCE, lambda: pytest.fail("cancelled task ran"))
        proceed.set()
        metrics = running.result(timeout=3)
        assert waiting.cancelled()
        assert metrics["closed"] is True and metrics["pending"] == 0
        assert metrics["active"] == 1  # caller is still running
        for thread in pool._threads:
            thread.join(timeout=3)
        assert all(not thread.is_alive() for thread in pool._threads)
    finally:
        proceed.set()
        for thread in pool._threads:
            thread.join(timeout=3)


def test_shutdown_wait_after_earlier_nonblocking_close_joins_workers():
    """Closing twice is idempotent, but a later waiter must actually wait."""
    pool = PriorityExecutor(max_workers=1, max_pending=1,
                            thread_name_prefix="test-late-join")
    entered, release, waiting, joined = (threading.Event() for _ in range(4))
    closer = None

    def running():
        entered.set()
        assert release.wait(3)
        return "finished"

    try:
        task = pool.submit(TaskPriority.USER_ACTION, running)
        assert entered.wait(2)
        pool.shutdown(wait=False)
        assert pool.admission_snapshot()["closed"] is True

        def wait_again():
            waiting.set()
            pool.shutdown(wait=True)
            joined.set()

        closer = threading.Thread(target=wait_again)
        closer.start()
        assert waiting.wait(2)
        # The first close must not cause the second to return while the
        # original worker is still held by the user action.
        assert not joined.wait(0.1)
        release.set()
        assert task.result(timeout=3) == "finished"
        assert joined.wait(3)
        closer.join(timeout=3)
        assert not closer.is_alive()
        assert all(not t.is_alive() for t in pool._threads)
        assert pool.admission_snapshot()["pending"] == 0
        assert pool.admission_snapshot()["active"] == 0
    finally:
        release.set()
        if closer is not None:
            closer.join(timeout=3)
        pool.shutdown(wait=True)


def test_repeated_worker_shutdown_after_nonblocking_close_does_not_self_join():
    """A repeated worker call to shutdown(wait=True) must never join itself."""
    pool = PriorityExecutor(max_workers=1, thread_name_prefix="test-reclose")
    entered, resume = threading.Event(), threading.Event()

    def worker():
        entered.set()
        assert resume.wait(3)
        pool.shutdown(wait=True)
        return "done"

    try:
        result = pool.submit(TaskPriority.NORMAL, worker)
        assert entered.wait(2)
        pool.shutdown(wait=False)
        resume.set()
        assert result.result(timeout=3) == "done"
        pool.shutdown(wait=True)
        assert all(not t.is_alive() for t in pool._threads)
    finally:
        resume.set()
        pool.shutdown(wait=True)
