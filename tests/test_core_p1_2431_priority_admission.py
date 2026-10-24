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


def test_bounded_numeric_timing_snapshot_after_320_tasks():
    pool = PriorityExecutor(max_workers=1, thread_name_prefix="PRIVATE_ACCOUNT_TIMING")
    try:
        assert pool.timing_snapshot() == {
            "queue_wait": {"samples": 0, "p50_ms": 0.0, "p95_ms": 0.0},
            "worker": {"samples": 0, "p50_ms": 0.0, "p95_ms": 0.0},
        }
        futures = [pool.submit(TaskPriority.NORMAL, lambda: None)
                   for _ in range(320)]
        for future in futures:
            assert future.result(timeout=3) is None
        # Future.set_result() precedes the worker's finally block that
        # records its duration; join workers before inspecting final samples.
        pool.shutdown(wait=True)
        data = pool.timing_snapshot()
        for phase in ("queue_wait", "worker"):
            assert data[phase]["samples"] == 256
            assert 0.0 <= data[phase]["p50_ms"] <= data[phase]["p95_ms"]
        assert "PRIVATE_ACCOUNT_TIMING" not in repr(data)
        assert not pool._queued_at
        assert pool.admission_snapshot()["admitted"] == 320
    finally:
        pool.shutdown(wait=True)


def test_timing_excludes_cancelled_pending_and_includes_worker_failure():
    pool = PriorityExecutor(max_workers=1, max_pending=1,
                            thread_name_prefix="test-timing-cancel")
    entered, release = threading.Event(), threading.Event()

    def worker():
        entered.set()
        assert release.wait(3)
        raise ValueError("PRIVATE_FAILURE_DETAILS")

    try:
        running = pool.submit(TaskPriority.NORMAL, worker)
        assert entered.wait(2)
        pending = pool.submit(TaskPriority.MAINTENANCE, lambda: None)
        pool.shutdown(wait=False, cancel_futures=True)
        assert pending.cancelled()
        assert len(pool._queued_at) == 0
        release.set()
        with pytest.raises(ValueError, match="PRIVATE_FAILURE_DETAILS"):
            running.result(timeout=3)
        pool.shutdown(wait=True)
        metrics = pool.timing_snapshot()
        assert metrics["queue_wait"]["samples"] == 1
        assert metrics["worker"]["samples"] == 1
        assert "PRIVATE_FAILURE_DETAILS" not in repr(metrics)
        assert pool.admission_snapshot()["pending"] == 0
    finally:
        release.set()
        pool.shutdown(wait=True)


def test_none_callable_never_stops_a_worker_or_leaks_a_shared_admission_slot():
    """A None payload is the internal stop sentinel, not runnable work."""
    from cifereca.task_admission import SharedTaskAdmission

    admission = SharedTaskAdmission(max_inflight=1, reserve_user_slots=0)
    pool = PriorityExecutor(
        max_workers=1, max_pending=1, shared_admission=admission,
        admission_scope="PRIVATE_PROFILE",
    )
    try:
        initial = pool.admission_snapshot()
        for task in (None, False, "PRIVATE_SIGNED_URL", object()):
            with pytest.raises(TypeError, match="callable"):
                pool.submit(TaskPriority.USER_ACTION, task)
            assert pool.admission_snapshot() == initial
            assert admission.snapshot()["inflight"] == 0
            assert admission.snapshot()["rejected"] == 0
        result = pool.submit(TaskPriority.USER_ACTION, lambda: "worker-survived")
        assert result.result(timeout=3) == "worker-survived"
        assert pool.admission_snapshot()["admitted"] == 1
    finally:
        pool.shutdown(wait=True)
    assert admission.snapshot()["inflight"] == 0
    assert all(not worker.is_alive() for worker in pool._threads)
    assert "PRIVATE_PROFILE" not in repr(admission.snapshot())


@pytest.mark.parametrize("bad_priority", (
    True, False, 1.5, "0", None, object(),
))
def test_priority_is_integral_and_checked_before_any_admission(bad_priority):
    from cifereca.task_admission import SharedTaskAdmission

    admission = SharedTaskAdmission(max_inflight=1, reserve_user_slots=0)
    pool = PriorityExecutor(
        max_workers=1, shared_admission=admission,
        admission_scope="PRIVATE_SCOPE",
    )
    try:
        with pytest.raises(ValueError, match="invalid task priority"):
            pool.submit(bad_priority, lambda: pytest.fail("malformed task ran"))
        assert pool.admission_snapshot()["admitted"] == 0
        assert pool.admission_snapshot()["pending"] == 0
        assert admission.snapshot()["inflight"] == 0
        assert pool.submit(0, lambda: "ok").result(timeout=3) == "ok"
    finally:
        pool.shutdown(wait=True)
    assert admission.snapshot()["inflight"] == 0


def test_plugin_supplied_priority_int_hook_is_never_called_under_pool_lock():
    """Calling arbitrary __int__ under the lock would enable a deadlock."""
    class HostilePriority:
        def __int__(self):
            raise AssertionError("PRIVATE_INT_HOOK_INVOKED")

    pool = PriorityExecutor(max_workers=1)
    try:
        with pytest.raises(ValueError, match="invalid task priority"):
            pool.submit(HostilePriority(), lambda: None)
        assert pool.submit(TaskPriority.USER_ACTION, lambda: 42).result(timeout=3) == 42
    finally:
        pool.shutdown(wait=True)


def test_subclassed_int_priority_never_invokes_dynamic_hooks_or_enters_queue():
    class PluginPriority(int):
        def __int__(self):
            raise AssertionError("PRIVATE_INT_HOOK")

        def __lt__(self, other):
            raise AssertionError("PRIVATE_COMPARISON_HOOK")

    from cifereca.task_admission import SharedTaskAdmission

    admission = SharedTaskAdmission(max_inflight=1, reserve_user_slots=0)
    pool = PriorityExecutor(max_workers=1, shared_admission=admission)
    try:
        with pytest.raises(ValueError, match="invalid task priority"):
            pool.submit(PluginPriority(0), lambda: pytest.fail("not callable"))
        assert pool.admission_snapshot()["admitted"] == 0
        assert admission.snapshot()["inflight"] == 0
        assert pool.submit(TaskPriority.USER_ACTION, lambda: "ok").result(timeout=3) == "ok"
        assert pool.submit(40, lambda: "int-ok").result(timeout=3) == "int-ok"
    finally:
        pool.shutdown(wait=True)
    assert admission.snapshot()["inflight"] == 0


@pytest.mark.parametrize("callback_error", (SystemExit, KeyboardInterrupt))
@pytest.mark.parametrize("task_fails", (False, True))
def test_fatal_completion_callback_cannot_kill_worker_or_strand_shared_slots(
    callback_error, task_fails,
):
    """An untrusted callback must not kill the only worker after completion."""
    from cifereca.task_admission import SharedTaskAdmission

    admission = SharedTaskAdmission(max_inflight=2, reserve_user_slots=0)
    pool = PriorityExecutor(
        max_workers=1, shared_admission=admission,
        admission_scope="PRIVATE_ACCOUNT",
    )
    entered, release, callback_ran = (
        threading.Event(), threading.Event(), threading.Event()
    )

    def first_task():
        entered.set()
        assert release.wait(3)
        if task_fails:
            raise ValueError("PRIVATE_TASK_ERROR")
        return "first-ok"

    def fatal_callback(_future):
        callback_ran.set()
        raise callback_error("PRIVATE_CALLBACK_SECRET")

    try:
        first = pool.submit(TaskPriority.USER_ACTION, first_task)
        assert entered.wait(2)
        first.add_done_callback(fatal_callback)
        second = pool.submit(TaskPriority.USER_ACTION, lambda: "second-ok")
        release.set()
        assert callback_ran.wait(3)
        if task_fails:
            with pytest.raises(ValueError, match="PRIVATE_TASK_ERROR"):
                first.result(timeout=3)
        else:
            assert first.result(timeout=3) == "first-ok"
        assert second.result(timeout=3) == "second-ok"
        assert pool.admission_snapshot()["admitted"] == 2
    finally:
        release.set()
        pool.shutdown(wait=True)

    assert admission.snapshot()["inflight"] == 0
    assert admission.snapshot()["running"] == 0
    assert pool.admission_snapshot()["pending"] == 0
    assert all(not thread.is_alive() for thread in pool._threads)
    assert "PRIVATE_ACCOUNT" not in repr(admission.snapshot())


@pytest.mark.parametrize("callback_error", (SystemExit, KeyboardInterrupt))
def test_fatal_cancel_callback_cannot_strand_other_shutdown_futures(callback_error):
    """Drain all removed queued tasks, even if one callback raises BaseException."""
    from cifereca.task_admission import SharedTaskAdmission

    admission = SharedTaskAdmission(max_inflight=3, reserve_user_slots=0)
    pool = PriorityExecutor(max_workers=1, shared_admission=admission)
    entered, release, cancelled_seen = (
        threading.Event(), threading.Event(), threading.Event()
    )

    def blocking():
        entered.set()
        assert release.wait(3)
        return "finished"

    def fatal_cancel_callback(_future):
        cancelled_seen.set()
        raise callback_error("PRIVATE_CANCEL_TOKEN")

    try:
        running = pool.submit(TaskPriority.USER_ACTION, blocking)
        assert entered.wait(2)
        first = pool.submit(TaskPriority.MAINTENANCE, lambda: "never-first")
        second = pool.submit(TaskPriority.MAINTENANCE, lambda: "never-second")
        first.add_done_callback(fatal_cancel_callback)
        pool.shutdown(wait=False, cancel_futures=True)
        assert cancelled_seen.is_set()
        assert first.cancelled() and second.cancelled()
        assert pool.admission_snapshot()["pending"] == 0
        release.set()
        assert running.result(timeout=3) == "finished"
    finally:
        release.set()
        pool.shutdown(wait=True)

    assert admission.snapshot()["inflight"] == 0
    assert admission.snapshot()["running"] == 0
    assert all(not thread.is_alive() for thread in pool._threads)
