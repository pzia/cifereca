"""P1 #2431/#1741: scoped admission rejects are observable only as counts."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from cifereca.task_admission import SharedTaskAdmission


def test_mutually_exclusive_rejection_reasons_and_user_lane():
    admission = SharedTaskAdmission(
        max_inflight=3, reserve_user_slots=1, max_per_scope=1,
    )
    first = admission.acquire(priority=40, scope="PRIVATE_ACCOUNT_A")
    second = admission.acquire(priority=60, scope="PRIVATE_ACCOUNT_B")
    assert first is not None and second is not None

    # Reserved user slot: a third background job cannot consume it.
    assert admission.acquire(priority=40, scope="PRIVATE_ACCOUNT_C") is None
    # The remaining slot cannot be consumed twice by scope A.
    assert admission.acquire(priority=0, scope="PRIVATE_ACCOUNT_A") is None
    last = admission.acquire(priority=0, scope="PRIVATE_ACCOUNT_C")
    assert last is not None
    # Now the entire pool is occupied, regardless of the proposed scope.
    assert admission.acquire(priority=0, scope="PRIVATE_ACCOUNT_D") is None

    snapshot = admission.snapshot()
    assert snapshot["inflight"] == 3
    assert snapshot["background"] == 2
    assert snapshot["active_scopes"] == 3
    assert snapshot["admitted"] == 3
    assert snapshot["rejected"] == 3
    assert (
        snapshot["rejected_global"],
        snapshot["rejected_reserved"],
        snapshot["rejected_scope"],
    ) == (1, 1, 1)
    assert (snapshot["rejected_user"], snapshot["rejected_background"]) == (2, 1)
    assert "PRIVATE" not in repr(snapshot) + repr(admission)

    for lease in (first, second, last):
        lease.release()
        lease.release()  # idempotent
    completed = admission.snapshot()
    assert completed["inflight"] == completed["background"] == 0
    assert completed["active_scopes"] == 0
    # Historical aggregate refusals remain available after completion.
    assert completed["rejected"] == snapshot["rejected"]
    assert completed["rejected_scope"] == 1
    assert completed["rejected_global"] == 1


def test_no_user_reservation_when_disabled_and_priority_boundary_is_stable():
    admission = SharedTaskAdmission(max_inflight=2, reserve_user_slots=0)
    first = admission.acquire(priority=29)
    second = admission.acquire(priority=30)
    assert first is not None and second is not None
    assert admission.snapshot()["background"] == 1
    assert admission.acquire(priority=29) is None
    assert admission.acquire(priority=30) is None
    snap = admission.snapshot()
    assert snap["rejected"] == 2
    assert snap["rejected_global"] == 2
    assert snap["rejected_reserved"] == 0
    assert snap["rejected_scope"] == 0
    assert snap["rejected_user"] == snap["rejected_background"] == 1
    first.release()
    second.release()


def test_scope_rejection_does_not_claim_global_saturation():
    admission = SharedTaskAdmission(
        max_inflight=10, reserve_user_slots=1, max_per_scope=2,
    )
    leases = [
        admission.acquire(priority=0, scope="PRIVATE_SCOPE"),
        admission.acquire(priority=40, scope="PRIVATE_SCOPE"),
    ]
    assert all(lease is not None for lease in leases)
    assert admission.acquire(priority=60, scope="PRIVATE_SCOPE") is None
    snap = admission.snapshot()
    assert snap["rejected_scope"] == 1
    assert snap["rejected_global"] == snap["rejected_reserved"] == 0
    assert snap["rejected_background"] == 1
    assert "PRIVATE_SCOPE" not in repr(snap)
    for lease in leases:
        lease.release()


def test_concurrent_contention_counters_remain_atomic_without_stored_scope():
    admission = SharedTaskAdmission(max_inflight=1, reserve_user_slots=0)
    running = admission.acquire(priority=0, scope="PRIVATE_RUNNING")
    assert running is not None

    def reject(index):
        return admission.acquire(priority=40 if index % 2 else 0,
                                 scope=f"PRIVATE_USER_{index}")

    with ThreadPoolExecutor(max_workers=8) as pool:
        attempts = list(pool.map(reject, range(80)))
    assert attempts == [None] * 80
    snapshot = admission.snapshot()
    assert snapshot["rejected"] == 80
    assert snapshot["rejected_global"] == 80
    assert snapshot["rejected_reserved"] == snapshot["rejected_scope"] == 0
    assert snapshot["rejected_user"] == snapshot["rejected_background"] == 40
    assert "PRIVATE_USER" not in repr(snapshot)
    running.release()
    assert admission.snapshot()["inflight"] == 0
