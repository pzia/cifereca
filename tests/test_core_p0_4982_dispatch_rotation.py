"""#4982 deterministic autonomous class-rotation receipts.

Pages are synthetic journal hints; the admission test uses the existing
SharedTaskAdmission. No registered producer, network, GTK or dispatcher is
activated. The policy's full composition is tested after parent merges.
"""
from collections import Counter
from dataclasses import replace

import pytest

from cifereca.core import notice_c_job_journal as journal
from cifereca.core.notice_c_dispatch_rotation import (
    PublicDispatchRotation, RotationOffer, RotationPage,
)
from cifereca.task_admission import SharedTaskAdmission
from cifereca.task_queue import TaskPriority

KINDS = ("fetch_source_notice", "reparse_cached_input", "relink_notices", "recompute_aggregate")


class JournalView:
    def __init__(self, rows):
        self.rows = {ref.key: (kind, ref) for kind, ref in rows}
        self.pages = []

    def read(self, kind, after, limit):
        self.pages.append((kind, after, limit))
        keys = sorted(key for key in self.rows if after is None or key > after)[:limit]
        refs = tuple(self.rows[key][1] for key in keys if self.rows[key][0] == kind)
        return RotationPage(refs, keys[-1] if len(keys) == limit else None,
                            len(keys), 0)

    def replace(self, ref):
        kind, previous = self.rows[ref.key]
        assert previous == ref
        self.rows[ref.key] = (kind, replace(ref, generation=ref.generation+1))


def row(kind, index):
    return kind, journal.JobRef(f"{index:064x}", 1)


def test_one_available_slot_all_four_classes_progress_under_permanent_burst():
    # Each accepted logical key is immediately made ready with a newer
    # generation: no class ever drains, and a hot key cannot monopolize.
    view = JournalView([row(KINDS[i%4], i+1) for i in range(96)])
    policy = PublicDispatchRotation(KINDS)
    accepted = []
    for _ in range(160):
        offered = []

        def offer(ref):
            if offered:
                return RotationOffer("blocked")
            offered.append(ref)
            accepted.append(ref)
            view.replace(ref)
            return RotationOffer("accepted", ref)

        tick = policy.tick(view.read, offer)
        assert tick.examined <= 32
        assert len(tick.handles) <= 1
        assert sum(page[2] for page in view.pages[-4:]) == 32

    by_kind = Counter(view.rows[ref.key][0] for ref in accepted)
    assert len(by_kind) == 4 and min(by_kind.values()) >= 24
    assert set(ref.key for ref in accepted) == set(view.rows)
    # Every logical key is seen despite immediately re-admitted generations;
    # no promise of a wall-time bound while slots are unavailable.


def test_sparse_class_scans_while_other_class_is_blocked():
    # 80 rows of unrelated/unsupported work precede one rare eligible
    # class. It must get a cursor slice even on ticks with full admission.
    view = JournalView([row("unsupported", i+1) for i in range(80)]
                       + [row(KINDS[0], 81), row(KINDS[3], 82)])
    policy = PublicDispatchRotation((KINDS[0], KINDS[3]))
    blocked = []
    for _ in range(7):
        def refuse(ref):
            blocked.append(ref)
            return RotationOffer("blocked")
        tick = policy.tick(view.read, refuse)
        assert tick.examined <= 32 and not tick.handles
    assert blocked
    accepted = []
    for _ in range(12):
        def offer(ref):
            accepted.append(ref)
            del view.rows[ref.key]
            return RotationOffer("accepted", ref)
        tick = policy.tick(view.read, offer)
        assert tick.examined <= 32 and len(tick.handles) <= 2
        if len(accepted) == 2:
            break
    assert {int(ref.key, 16) for ref in accepted} == {81, 82}


def test_duplicate_skips_without_consuming_slot_or_losing_next_candidate():
    view = JournalView([row(KINDS[1], i) for i in range(1, 5)])
    policy = PublicDispatchRotation((KINDS[1],))
    duplicate, first, second, blocked = tuple(ref for _, ref in view.rows.values())
    offered = []

    def offer(ref):
        offered.append(ref)
        if ref == duplicate:
            return RotationOffer("duplicate")
        return RotationOffer("accepted", ref)

    tick = policy.tick(view.read, offer)
    assert offered == [duplicate, first, second]
    assert tick.handles == (first, second) and tick.duplicates == 1 and tick.deferred

    offered.clear()
    tick = policy.tick(view.read, offer)
    assert offered == [blocked] and tick.handles == (blocked,)
    assert not tick.deferred


def test_refusal_revisits_first_unsubmitted_class_and_keeps_user_reserve():
    view = JournalView([row(KINDS[i], i+1) for i in range(4)])
    policy = PublicDispatchRotation(KINDS)
    budget = SharedTaskAdmission(max_inflight=2, reserve_user_slots=1)
    alive = []
    offers = []

    def offer(ref):
        offers.append(ref)
        lease = budget.acquire(priority=int(TaskPriority.METADATA))
        if lease is None:
            return RotationOffer("blocked")
        alive.append(lease)
        return RotationOffer("accepted", ref)

    try:
        for expected in range(1, 5):
            offers.clear()
            tick = policy.tick(view.read, offer)
            assert len(tick.handles) == 1
            ref, = tick.handles
            assert int(ref.key, 16) == expected
            user = budget.acquire(priority=int(TaskPriority.USER_ACTION))
            assert user is not None
            try:
                assert budget.snapshot()["inflight"] == 2
                if expected < 4:
                    # Every refused tick must keep the next class first.
                    retry = policy.tick(view.read, offer)
                    assert retry.deferred and not retry.handles
                    assert int(offers[-1].key, 16) == expected + 1
            finally:
                user.release()
            del view.rows[ref.key]
            alive.pop().release()
        assert budget.snapshot()["inflight"] == 0
    finally:
        for lease in alive:
            lease.release()


def test_validation_and_cancellation_do_not_advance_unoffered_candidate():
    policy = PublicDispatchRotation((KINDS[0],))
    view = JournalView([row(KINDS[0], 1), row(KINDS[0], 2)])
    with pytest.raises(ValueError, match="invalid_kinds"):
        PublicDispatchRotation((KINDS[0], KINDS[0]))
    ref = view.rows[f"{1:064x}"][1]
    with pytest.raises(ValueError, match="invalid_page"):
        policy.tick(lambda *_: RotationPage((ref,), None, 0, 0),
                    lambda _: pytest.fail("invalid hint admitted"))
    with pytest.raises(ValueError, match="invalid_offer"):
        policy.tick(view.read, lambda _: RotationOffer("accepted"))
    calls = []
    accepted = []
    def check():
        calls.append(1)
        if len(calls) == 3:
            raise RuntimeError("cancelled")
    def offer(candidate):
        accepted.append(candidate)
        return RotationOffer("accepted", candidate)
    with pytest.raises(RuntimeError, match="cancelled"):
        policy.tick(view.read, offer, check_cancelled=check)
    assert accepted == [ref]
    assert policy.tick(view.read, offer).handles == (view.rows[f"{2:064x}"][1],)


def test_descriptive_journal_kind_not_admitted_to_public_rotation():
    assert "consolidate_descriptive" in journal._KINDS
    with pytest.raises(ValueError, match="invalid_kinds"):
        PublicDispatchRotation(("consolidate_descriptive",))


def test_circuit_blocked_preserves_cursor_and_does_not_stop_local_lane():
    network_kind, local_kind = KINDS[0], KINDS[3]
    view = JournalView([row(network_kind, 1), row(network_kind, 2),
                        row(local_kind, 3)])
    policy = PublicDispatchRotation((network_kind, local_kind))
    blocked = True

    def offer_lane(kind, ref):
        if kind == network_kind and blocked:
            return RotationOffer("circuit_blocked")
        del view.rows[ref.key]
        return RotationOffer("accepted", ref)

    tick = policy.tick(view.read, lambda _: pytest.fail("wrong offer port"),
                       offer_lane=offer_lane)
    assert tick.deferred and [int(ref.key, 16) for ref in tick.handles] == [3]
    blocked = False
    tick = policy.tick(view.read, lambda _: pytest.fail("wrong offer port"),
                       offer_lane=offer_lane)
    assert [int(ref.key, 16) for ref in tick.handles] == [1, 2]


def test_network_circuit_lanes_both_progress_with_continuous_local_work():
    from cifereca.core.notice_c_dispatch_rotation import _PURCHASE_LANE

    fetch, local = KINDS[0], KINDS[3]
    lanes = (fetch, local, _PURCHASE_LANE)
    view = JournalView([row(kind, index + 1)
                        for index, kind in enumerate(lanes * 3)])
    policy = PublicDispatchRotation(lanes)
    counts = Counter()
    seen = set()
    for _ in range(48):
        network_used = False

        def offer_lane(kind, ref):
            nonlocal network_used
            if kind != local:
                if network_used:
                    return RotationOffer("circuit_blocked")
                network_used = True
            counts[kind] += 1
            seen.add(ref.key)
            view.replace(ref)
            return RotationOffer("accepted", ref)

        tick = policy.tick(view.read, lambda _: pytest.fail("wrong offer port"),
                           offer_lane=offer_lane)
        assert len(tick.handles) <= 2 and tick.examined <= 32
    assert counts[local] == 48
    assert min(counts[fetch], counts[_PURCHASE_LANE]) >= 16
    assert seen == set(view.rows)
