"""Q11 promotion never replaces the bounded fair sweep or broker admission."""
from dataclasses import replace

import pytest

from cifereca.core.notice_c_dispatch_rotation import (
    PublicDispatchRotation, RotationOffer, validate_priority_hints,
)
from tests.test_core_p0_4982_dispatch_rotation import JournalView, row, KINDS


def test_priority_order_with_one_slot_and_continuous_visible_burst():
    kind = KINDS[3]
    view = JournalView([row(kind, i) for i in range(1, 9)])
    policy = PublicDispatchRotation((kind,))
    accepted = []
    for _ in range(32):
        offered = []
        refs = [view.rows[f"{i:064x}"][1] for i in (8, 7, 6)]
        hints = tuple(zip(refs, ("interactive_visible", "recent_acquisition",
                                 "author_neighborhood")))

        def offer(ref):
            if offered:
                return RotationOffer("blocked")
            offered.append(ref)
            accepted.append(ref)
            view.replace(ref)
            return RotationOffer("accepted", ref)

        tick = policy.tick(view.read, offer, priority_hints=hints)
        assert tick.examined <= 32 and len(tick.handles) <= 1
    assert accepted[0].key == f"{8:064x}"
    assert {ref.key for ref in accepted} == set(view.rows)


@pytest.mark.parametrize("reasons", [
    ("author_neighborhood", "recent_acquisition", "interactive_visible"),
    ("background_stale", "author_neighborhood", "recent_acquisition"),
])
def test_priority_is_generation_specific_and_ranks_scanned_candidates(reasons):
    kind = KINDS[3]
    view = JournalView([row(kind, i) for i in range(1, 5)])
    policy = PublicDispatchRotation((kind,))
    refs = [view.rows[f"{i:064x}"][1] for i in (2, 3, 4)]
    stale = replace(view.rows[f"{1:064x}"][1], generation=2)
    hints = ((stale, "interactive_visible"), *tuple(zip(refs, reasons)))
    offered = []
    def offer(ref):
        offered.append(ref)
        return RotationOffer("accepted", ref)
    tick = policy.tick(view.read, offer, priority_hints=hints)
    assert offered[0] == refs[-1]
    assert len(tick.handles) == 2
    assert tick.examined <= 32


def test_reject_invalid_hints_before_scan():
    _, ref = row(KINDS[3], 1)
    for hints in ([(ref, "interactive_visible")], ((ref, "invalid"),),
                  ((ref, "interactive_visible"), (ref, "recent_acquisition"))):
        with pytest.raises(ValueError, match="invalid_priority_hints"):
            validate_priority_hints(hints)
