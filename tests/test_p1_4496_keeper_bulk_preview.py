"""#4496: 'Tout accepter' is a bounded UI choice, NEVER a delete plan."""
from dataclasses import replace

import pytest

from cifereca.core.canonical_keeper_review import CanonicalKeeperReview
from cifereca.core.canonical_keeper_bulk_preview import preview_keeper_bulk


def artifact(n):
    return "sha256:" + f"{n:064x}"


def review(n, *, keeper=None, candidates=None, excluded=0):
    candidates = candidates if candidates is not None else (f"copy-{n}",)
    return CanonicalKeeperReview(
        status="review_required", reason="aesthetic_preference_only",
        identity_id="alice", artifact_id=artifact(n),
        keeper_placement_id=keeper or f"keeper-{n}",
        candidate_placement_ids=tuple(candidates),
        eligible_copies=len(candidates) + 1, excluded_copies=excluded,
        executable=False,
    )


def blocked(n, reason="ambiguous_keeper_preference"):
    return CanonicalKeeperReview(
        status="blocked", reason=reason, identity_id="alice",
        artifact_id=artifact(n), eligible_copies=2, executable=False,
    )


def test_default_is_empty_selection_and_no_write_capability():
    actual = preview_keeper_bulk([review(1), blocked(2)], identity_id="alice")
    assert actual.preselected == ()
    assert len(actual.eligible) == 1
    assert actual.blocked == ((artifact(2), "ambiguous_keeper_preference"),)
    assert actual.executable is False
    assert not hasattr(actual, "consent")
    assert not hasattr(actual, "transaction")


def test_accept_all_selects_only_reviews_and_preserves_blocked_counts():
    chosen = preview_keeper_bulk(
        [blocked(3), review(2, excluded=2), review(1), blocked(4)],
        identity_id="alice", accept_all=True,
    )
    assert [x.artifact_id for x in chosen.preselected] == [artifact(1), artifact(2)]
    assert chosen.preselected == chosen.eligible
    assert len(chosen.blocked) == 2
    assert chosen.excluded_copies == 2
    assert chosen.executable is False


def test_order_invariance_and_deterministic_candidate_sorting():
    one = review(1, candidates=("c2", "c1"))
    two = review(2, candidates=("c4", "c3"))
    direct = preview_keeper_bulk([two, one], identity_id="alice", accept_all=True)
    reverse = preview_keeper_bulk([one, two], identity_id="alice", accept_all=True)
    assert direct == reverse
    assert direct.preselected[0].retire_candidate_ids == ("c1", "c2")


@pytest.mark.parametrize("change", [
    {"status": "success"}, {"reason": "borrowable"},
    {"executable": True}, {"eligible_copies": 10},
    {"keeper_placement_id": ""}, {"keeper_placement_id": "copy-1"},
    {"candidate_placement_ids": ()},
    {"candidate_placement_ids": ("copy-1", "copy-1")},
    {"candidate_placement_ids": ["copy-1"]},
    {"identity_id": "bob"},
    {"artifact_id": "sha256:" + "G" * 64},
    {"reason": "aesthetic preference only"},
])
def test_malformed_or_overprivileged_advice_is_never_preselected(change):
    with pytest.raises(ValueError, match="invalid_keeper_bulk"):
        preview_keeper_bulk(
            [replace(review(1), **change)], identity_id="alice", accept_all=True,
        )


def test_blocked_entry_with_candidate_payload_is_rejected():
    forged = replace(
        blocked(1), keeper_placement_id="keeper-1",
        candidate_placement_ids=("copy-1",),
    )
    with pytest.raises(ValueError, match="invalid_keeper_bulk_blocked"):
        preview_keeper_bulk([forged], identity_id="alice", accept_all=True)


def test_duplicate_artifact_and_cross_artifact_placement_collision_reject_batch():
    one = review(1)
    with pytest.raises(ValueError, match="invalid_keeper_bulk_review"):
        preview_keeper_bulk([one, one], identity_id="alice", accept_all=True)
    other = review(2, keeper="copy-1")
    with pytest.raises(ValueError, match="overlapping_keeper_bulk_placements"):
        preview_keeper_bulk([one, other], identity_id="alice", accept_all=True)


def test_wrong_scope_or_invalid_batch_type_is_not_a_partial_selection():
    for value in (None, {}, [review(1)] * 257):
        with pytest.raises(ValueError, match="invalid_keeper_bulk_scope"):
            preview_keeper_bulk(value, identity_id="alice", accept_all=True)
    with pytest.raises(ValueError, match="invalid_keeper_bulk_scope"):
        preview_keeper_bulk([review(1)], identity_id="alice", accept_all=1)


def test_retirement_candidate_budget_is_an_all_or_nothing_guard():
    rows = [
        review(n + 1, candidates=tuple(
            f"candidate-{n}-{index}" for index in range(9)
        ))
        for n in range(256)
    ]
    with pytest.raises(ValueError, match="keeper_bulk_budget_exceeded"):
        preview_keeper_bulk(rows, identity_id="alice", accept_all=True)


def test_unknown_identity_or_malformed_review_never_creates_choice():
    with pytest.raises(ValueError, match="invalid_keeper_bulk_review"):
        preview_keeper_bulk([None], identity_id="alice", accept_all=True)
    with pytest.raises(ValueError, match="invalid_keeper_bulk_scope"):
        preview_keeper_bulk([review(1)], identity_id="", accept_all=True)
