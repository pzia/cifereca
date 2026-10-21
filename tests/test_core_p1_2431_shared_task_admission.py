"""P1 #2431: deterministic opt-in admission across Core/GUI/plugin pools."""
from __future__ import annotations

import threading

import pytest

from cifereca.task_admission import SharedTaskAdmission
from cifereca.task_queue import PriorityExecutor, TaskPriority
from cifereca.task_runtime import TaskBrokerImpl


def test_shared_budget_reserves_user_lane_across_independent_pools():
    budget = SharedTaskAdmission(
        max_inflight=3, reserve_user_slots=1, max_per_scope=2,
    )
    source = PriorityExecutor(
        max_workers=1, shared_admission=budget,
        admission_scope="PRIVATE_PROVIDER_ACCOUNT",
    )
    user = PriorityExecutor(
        max_workers=1, shared_admission=budget,
        admission_scope="OTHER_PRIVATE_PROVIDER",
    )
    entered, release = threading.Event(), threading.Event()
    try:
        def slow():
            entered.set()
            assert release.wait(3)
        first = source.submit(TaskPriority.NORMAL, slow)
        assert entered.wait(2)
        second = source.submit(TaskPriority.METADATA, lambda: "queued")
        with pytest.raises(RuntimeError, match="shared task admission full"):
            source.submit(TaskPriority.USER_ACTION, lambda: "unexpected")
        with pytest.raises(RuntimeError, match="shared task admission full"):
            user.submit(TaskPriority.MAINTENANCE, lambda: "unexpected")
        action = user.submit(TaskPriority.USER_ACTION, lambda: "user")
        assert action.result(timeout=3) == "user"
        user.shutdown(wait=True)
        data = budget.snapshot()
        assert data["inflight"] == 2
        assert data["background"] == 2
        assert data["rejected"] == 2
        assert data["active_scopes"] == 1
        assert "PRIVATE" not in repr(data) + repr(budget)
        release.set()
        assert first.result(timeout=3) is None
        assert second.result(timeout=3) == "queued"
        source.shutdown(wait=True)
        assert budget.snapshot()["inflight"] == 0
        assert budget.snapshot()["active_scopes"] == 0
    finally:
        release.set()
        source.shutdown(wait=True)
        user.shutdown(wait=True)


def test_pending_cancel_releases_shared_slot_without_waiting_for_worker():
    budget = SharedTaskAdmission(max_inflight=2, reserve_user_slots=0)
    pool_a = PriorityExecutor(max_workers=1, shared_admission=budget)
    pool_b = PriorityExecutor(max_workers=1, shared_admission=budget)
    entered, release = threading.Event(), threading.Event()
    try:
        def slow():
            entered.set()
            assert release.wait(3)
        first = pool_a.submit(TaskPriority.NORMAL, slow)
        assert entered.wait(2)
        pending = pool_a.submit(TaskPriority.NORMAL, lambda: "not-run")
        assert budget.snapshot()["inflight"] == 2
        with pytest.raises(RuntimeError, match="shared task admission full"):
            pool_b.submit(TaskPriority.NORMAL, lambda: None)
        assert pending.cancel()
        assert budget.snapshot()["inflight"] == 1
        other = pool_b.submit(TaskPriority.NORMAL, lambda: "other")
        assert other.result(timeout=3) == "other"
        release.set()
        assert first.result(timeout=3) is None
        pool_a.shutdown(wait=True)
        pool_b.shutdown(wait=True)
        assert budget.snapshot()["inflight"] == 0
        assert budget.snapshot()["admitted"] == 3
    finally:
        release.set()
        pool_a.shutdown(wait=True)
        pool_b.shutdown(wait=True)


def test_worker_errors_and_shutdown_release_global_reservations():
    budget = SharedTaskAdmission(max_inflight=2, reserve_user_slots=0)
    pool = PriorityExecutor(max_workers=1, shared_admission=budget)
    entered, release = threading.Event(), threading.Event()
    try:
        def failing():
            entered.set()
            assert release.wait(3)
            raise ValueError("PRIVATE_ERROR")
        running = pool.submit(TaskPriority.NORMAL, failing)
        assert entered.wait(2)
        waiting = pool.submit(TaskPriority.MAINTENANCE, lambda: "unexpected")
        pool.shutdown(wait=False, cancel_futures=True)
        assert waiting.cancelled()
        assert budget.snapshot()["inflight"] == 1
        release.set()
        with pytest.raises(ValueError):
            running.result(timeout=3)
        pool.shutdown(wait=True)
        assert budget.snapshot()["inflight"] == 0
        assert "PRIVATE_ERROR" not in repr(budget.snapshot())
    finally:
        release.set()
        pool.shutdown(wait=True)


def test_nested_submission_over_capacity_fails_fast_without_deadlock():
    budget = SharedTaskAdmission(max_inflight=1, reserve_user_slots=0)
    first = PriorityExecutor(max_workers=1, shared_admission=budget)
    second = PriorityExecutor(max_workers=1, shared_admission=budget)
    try:
        def nested():
            with pytest.raises(RuntimeError, match="shared task admission full"):
                second.submit(TaskPriority.USER_ACTION, lambda: "never")
            return "not-blocked"
        job = first.submit(TaskPriority.NORMAL, nested)
        assert job.result(timeout=3) == "not-blocked"
        first.shutdown(wait=True)
        second.shutdown(wait=True)
        assert budget.snapshot()["inflight"] == 0
    finally:
        first.shutdown(wait=True)
        second.shutdown(wait=True)


def test_broker_and_executor_can_share_a_single_budget():
    budget = SharedTaskAdmission(max_inflight=1, reserve_user_slots=0)
    broker = TaskBrokerImpl(
        object(), max_workers=1, shared_admission=budget,
        admission_scope="PRIVATE_BROKER",
    )
    pool = PriorityExecutor(
        max_workers=1, shared_admission=budget,
        admission_scope="PRIVATE_OTHER",
    )
    entered, release = threading.Event(), threading.Event()
    try:
        def slow(_control):
            entered.set()
            assert release.wait(3)
        task = broker.submit(slow)
        assert entered.wait(2)
        with pytest.raises(RuntimeError, match="shared task admission full"):
            pool.submit(TaskPriority.NORMAL, lambda: None)
        release.set()
        assert task.result(timeout=3) is None
        broker.close()
        pool.shutdown(wait=True)
        assert budget.snapshot()["inflight"] == 0
    finally:
        release.set()
        broker.close()
        pool.shutdown(wait=True)


@pytest.mark.parametrize("kwargs", [
    {"max_inflight": 0}, {"max_inflight": True},
    {"max_inflight": 2, "reserve_user_slots": 2},
    {"max_inflight": 2, "reserve_user_slots": -1},
    {"max_inflight": 2, "max_per_scope": 0},
])
def test_invalid_budget_fails_before_any_pool_starts(kwargs):
    with pytest.raises(ValueError):
        SharedTaskAdmission(**kwargs)


def test_legacy_executor_defaults_remain_independent():
    pool = PriorityExecutor(max_workers=1)
    try:
        assert pool.submit(TaskPriority.NORMAL, lambda: "legacy").result(timeout=3) == "legacy"
        assert pool.admission_snapshot()["max_pending"] is None
    finally:
        pool.shutdown(wait=True)
