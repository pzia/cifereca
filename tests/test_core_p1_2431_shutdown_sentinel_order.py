"""#2431: shutdown without cancellation must drain ALL admitted priorities.

No GTK, provider network, process-wide overrides or user data. Integer
priorities are public API; even a very large valid integer must not sort
behind an early worker-stop sentinel and leak a Future/admission lease.
"""
from __future__ import annotations

import threading

import pytest

from cifereca.task_admission import SharedTaskAdmission
from cifereca.task_queue import PriorityExecutor, TaskPriority


@pytest.mark.parametrize("large_priority", [10_001, 10**100])
def test_non_cancelling_shutdown_drains_large_integer_priority(
    large_priority,
):
    executor = PriorityExecutor(max_workers=1, max_pending=1)
    entered, release = threading.Event(), threading.Event()
    observed = []
    try:
        def blocking():
            entered.set()
            assert release.wait(3)
            observed.append("running")

        running = executor.submit(TaskPriority.NORMAL, blocking)
        assert entered.wait(2)
        queued = executor.submit(
            large_priority, lambda: observed.append("queued"),
        )
        executor.shutdown(wait=False, cancel_futures=False)
        assert not queued.cancelled()
        assert executor.admission_snapshot()["pending"] == 1
        release.set()
        assert running.result(timeout=3) is None
        assert queued.result(timeout=3) is None
        executor.shutdown(wait=True)
        assert observed == ["running", "queued"]
        assert executor.admission_snapshot()["pending"] == 0
        assert executor.admission_snapshot()["active"] == 0
    finally:
        release.set()
        executor.shutdown(wait=True)


def test_shared_reservations_and_priority_order_survive_drain_on_shutdown():
    admission = SharedTaskAdmission(
        max_inflight=3, reserve_user_slots=1,
    )
    executor = PriorityExecutor(
        max_workers=1, max_pending=2, shared_admission=admission,
        admission_scope="PRIVATE_ACCOUNT",
    )
    entered, release = threading.Event(), threading.Event()
    observed = []
    try:
        def blocking():
            entered.set()
            assert release.wait(3)
            observed.append("running")

        running = executor.submit(TaskPriority.NORMAL, blocking)
        assert entered.wait(2)
        last_priority = executor.submit(
            10**100, lambda: observed.append("last_priority"),
        )
        user_priority = executor.submit(
            TaskPriority.USER_ACTION, lambda: observed.append("user"),
        )
        assert admission.snapshot()["inflight"] == 3
        executor.shutdown(wait=False, cancel_futures=False)
        release.set()
        assert running.result(timeout=3) is None
        assert user_priority.result(timeout=3) is None
        assert last_priority.result(timeout=3) is None
        executor.shutdown(wait=True)
        assert observed == ["running", "user", "last_priority"]
        assert admission.snapshot()["inflight"] == 0
        assert admission.snapshot()["active_scopes"] == 0
        assert executor.admission_snapshot()["pending"] == 0
        assert "PRIVATE" not in repr(admission.snapshot())
    finally:
        release.set()
        executor.shutdown(wait=True)
