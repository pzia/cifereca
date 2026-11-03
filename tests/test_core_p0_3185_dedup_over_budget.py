"""P0 #3185: bounded post-B duplicate probe must never invent an empty set."""
import logging

import pytest

from cifereca.core.canonical_post_placement import (
    verified_post_placement_duplicates,
)
from cifereca.core.convergence_execution import _check_post_placement_duplicates
from tests.test_core_p0_3171_atomic_publication_v14 import BYTES, store
from tests.test_core_p0_3719_post_placement_duplicates import _two_accounts


def _append_candidates(storage, root, artifact_id, count, *, start=0):
    """Add registered-only copies: none is valid physical evidence."""
    with storage._lock, storage._connect() as db:
        db.executemany(
            "INSERT INTO placements(placement_id,artifact_id,path,identity_id,"
            "state,metadata_json,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            [
                (f"overflow-placement-{n}", artifact_id,
                 str(root / "A" / f"unverified-{n}.pdf"), "A",
                 "present", "{}", 1, 1)
                for n in range(start, start + count)
            ],
        )


def test_exact_budget_still_verifies_real_same_profile_copy(store, tmp_path):
    root, paths, (first, second, foreign) = _two_accounts(store, tmp_path)
    # One genuine other Placement plus 63 unverified registrations = 64.
    _append_candidates(store, root, first.artifact_id, 63)
    assert verified_post_placement_duplicates(
        store, library_root=root, placement_id=first.placement_id,
    ) == (second.placement_id,)
    assert verified_post_placement_duplicates(
        store, library_root=root, placement_id=foreign.placement_id,
    ) == ()
    assert all(path.read_bytes() == BYTES for path in paths)


def test_overflow_is_not_no_duplicates_or_a_new_maintenance_permission(
    store, tmp_path, monkeypatch, caplog,
):
    from cifereca.core import canonical_post_placement

    root, paths, (first, second, foreign) = _two_accounts(store, tmp_path)
    # Create one legitimate advisory, then make the probe exceed its budget.
    _check_post_placement_duplicates(
        store, library_root=root, placement_id=first.placement_id,
    )
    with store._lock, store._connect() as db:
        record = db.execute(
            "SELECT key,value FROM metadata WHERE key LIKE "
            "'core.canonical-dedup-pending.v1.%'",
        ).fetchone()
    assert record is not None
    key, original = record
    _append_candidates(store, root, first.artifact_id, 64)

    def forbid_hash(*_args, **_kwargs):
        pytest.fail("oversized set must not hash an arbitrary subset")

    monkeypatch.setattr(canonical_post_placement, "_verified_copy", forbid_hash)
    # Previous successful checkpoint emits its own INFO. Scope captured logs
    # to the saturated replay rather than the entire fixture lifecycle.
    caplog.clear()
    with pytest.raises(ValueError, match="canonical_dedup_candidates_over_budget"):
        verified_post_placement_duplicates(
            store, library_root=root, placement_id=first.placement_id,
        )
    with caplog.at_level(logging.WARNING, logger="cifereca"):
        _check_post_placement_duplicates(
            store, library_root=root, placement_id=first.placement_id,
        )
    assert "canonical_dedup_over_budget" in caplog.text
    assert "canonical_dedup_pending" not in caplog.text
    assert store.metadata_get(key) == original  # no false resolution/overwrite
    assert store.placement_get(foreign.placement_id)["identity_id"] == "B"
    assert all(path.read_bytes() == BYTES for path in paths)
