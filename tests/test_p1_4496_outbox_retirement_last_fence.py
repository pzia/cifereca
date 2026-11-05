"""P1 #4496: final locked retirement and recovery may never touch Outbox.

Disposable real SQLite+bytes. Legacy destructive primitives are invoked only
by these synthetic safety tests; the ordinary production cleanup remains OFF.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from cifereca.core.canonical_dedup_retirement import (
    recover_canonical_duplicate_retirement,
)
from cifereca.locking import InterProcessLock
from tests.test_core_p0_3719_canonical_duplicate_retirement import rig, _retire
from tests.test_core_p0_3171_atomic_publication_v14 import BYTES, store


@pytest.mark.parametrize("excluded_side", ["candidate", "keeper"])
def test_locked_retirement_cannot_mutate_outbox_even_without_preview(
    rig, excluded_side,
):
    storage, root, paths, publications, _snapshot, _lock = rig
    target_idx = 1 if excluded_side == "candidate" else 0
    source_path = paths[target_idx]
    outbox = root / "A" / "Outbox" / "reader-1" / source_path.name
    outbox.parent.mkdir(parents=True)
    source_path.rename(outbox)
    with storage._lock, storage._connect() as db:
        db.execute(
            "UPDATE placements SET path=? WHERE placement_id=?",
            (str(outbox), publications[target_idx].placement_id),
        )
    try:
        with pytest.raises(ValueError, match="canonical_retire_excluded_path"):
            _retire(rig)
        assert outbox.read_bytes() == BYTES
        assert paths[1 - target_idx].read_bytes() == BYTES
        assert all(
            storage.placement_get(p.placement_id)["state"] == "present"
            for p in publications[:2]
        )
    finally:
        # The fixture is disposable; don't move the Outbox copy into another
        # profile or create a second identical provider entitlement.
        pass


def test_recovery_never_unlinks_candidate_if_keeper_relocated_into_outbox(
    rig, monkeypatch,
):
    storage, root, paths, publications, _snapshot, lock_path = rig
    candidate = paths[1]
    keeper = paths[0]
    original = Path.unlink

    def denied(path, *args, **kwargs):
        if path == candidate:
            raise PermissionError("synthetic pending tombstone")
        return original(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", denied)
        outcome = _retire(rig)
    assert outcome.cleanup_pending and not outcome.removed_file
    assert storage.placement_get(publications[1].placement_id)["state"] == "dedup_cleanup_pending"

    outbox = root / "A" / "Outbox" / "reader-1" / keeper.name
    outbox.parent.mkdir(parents=True)
    keeper.rename(outbox)
    with storage._lock, storage._connect() as db:
        db.execute(
            "UPDATE placements SET path=? WHERE placement_id=?",
            (str(outbox), publications[0].placement_id),
        )
    with InterProcessLock(lock_path) as lock:
        recovered = recover_canonical_duplicate_retirement(
            storage, library_root=root,
            candidate_placement_id=publications[1].placement_id,
            keeper_placement_id=publications[0].placement_id,
            shared_lock=lock,
        )
    assert recovered.cleanup_pending and not recovered.removed_file
    assert candidate.read_bytes() == BYTES
    assert outbox.read_bytes() == BYTES
    assert storage.placement_get(publications[1].placement_id)["state"] == "dedup_cleanup_pending"
