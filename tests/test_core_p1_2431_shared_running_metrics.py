"""Shared occupancy is measured on worker transitions, not sampled by a UI."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event

from cifereca.task_admission import SharedTaskAdmission
from cifereca.task_queue import PriorityExecutor


def assert_occupancy(budget, *, running, queued):
    data = budget.snapshot()
    assert data["running"] == running
    assert data["queued"] == queued
    assert data["inflight"] == running + queued
    assert all(type(data[name]) is int for name in (
        "running", "queued", "peak_running", "peak_queued", "peak_inflight",
    ))
    assert "PRIVATE" not in repr(data) + repr(budget)
    return data


def test_two_pools_share_exact_queued_running_and_peak_counts():
    budget = SharedTaskAdmission(max_inflight=4, reserve_user_slots=0)
    pools = [PriorityExecutor(max_workers=1, shared_admission=budget,
                              admission_scope=f"PRIVATE-{i}") for i in range(2)]
    entered = [Event(), Event()]
    release = Event()

    def blocking(index):
        entered[index].set()
        assert release.wait(4)

    try:
        active = [pool.submit(0, lambda i=i: blocking(i)) for i, pool in enumerate(pools)]
        assert all(event.wait(3) for event in entered)
        pending = [pool.submit(40, lambda: "queued") for pool in pools]
        data = assert_occupancy(budget, running=2, queued=2)
        assert (data["peak_inflight"], data["peak_running"], data["peak_queued"]) == (4, 2, 2)
        assert pending[0].cancel()
        assert_occupancy(budget, running=2, queued=1)
        release.set()
        for pool in pools:
            pool.shutdown(wait=True, cancel_futures=False)
        assert all(future.done() for future in active + pending)
        assert pending[1].result() == "queued"
        final = assert_occupancy(budget, running=0, queued=0)
        assert final["peak_inflight"] == 4
        assert final["peak_running"] == final["peak_queued"] == 2
    finally:
        release.set()
        for pool in pools:
            pool.shutdown(wait=True)


def test_future_done_callback_still_holds_running_lease_until_worker_exit():
    budget = SharedTaskAdmission(max_inflight=2, reserve_user_slots=0)
    pool = PriorityExecutor(max_workers=1, shared_admission=budget)
    entered, complete, callback_entered, callback_release = (Event() for _ in range(4))

    def operation():
        entered.set()
        assert complete.wait(4)
        return "done"

    def callback(_future):
        callback_entered.set()
        assert callback_release.wait(4)

    try:
        future = pool.submit(0, operation)
        assert entered.wait(3)
        future.add_done_callback(callback)
        pending = pool.submit(40, lambda: "must be cancelled")
        complete.set()
        assert callback_entered.wait(3)
        assert future.done()
        assert_occupancy(budget, running=1, queued=1)
        pool.shutdown(wait=False, cancel_futures=True)
        assert pending.cancelled()
        assert_occupancy(budget, running=1, queued=0)
        callback_release.set()
        pool.shutdown(wait=True)
        assert_occupancy(budget, running=0, queued=0)
    finally:
        complete.set()
        callback_release.set()
        pool.shutdown(wait=True)


def test_repeated_start_release_and_late_start_do_not_resurrect_lease():
    budget = SharedTaskAdmission(max_inflight=2, reserve_user_slots=0)
    lease = budget.acquire(priority=0, scope="PRIVATE")
    assert_occupancy(budget, running=0, queued=1)
    lease.mark_running()
    lease.mark_running()
    assert_occupancy(budget, running=1, queued=0)
    lease.release()
    lease.release()
    lease.mark_running()
    assert_occupancy(budget, running=0, queued=0)
    # Cancelling before start must not subtract another running task.
    queued = budget.acquire(priority=0)
    queued.release()
    assert_occupancy(budget, running=0, queued=0)


def test_parallel_transitions_preserve_global_count_and_peaks():
    budget = SharedTaskAdmission(max_inflight=32, reserve_user_slots=0)
    ready, release = Barrier(33), Event()

    def operation():
        lease = budget.acquire(priority=40, scope="PRIVATE")
        try:
            lease.mark_running()
            ready.wait(timeout=4)
            assert release.wait(4)
        finally:
            lease.release()
            lease.release()

    with ThreadPoolExecutor(max_workers=32) as pool:
        futures = [pool.submit(operation) for _ in range(32)]
        try:
            ready.wait(timeout=4)
            data = assert_occupancy(budget, running=32, queued=0)
            assert data["peak_running"] == data["peak_inflight"] == 32
        finally:
            release.set()
        for future in futures:
            future.result(timeout=4)
    assert_occupancy(budget, running=0, queued=0)
