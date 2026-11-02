"""#3185: confirmed retirement and tombstone recovery use bounded owner proof.

All filesystem actions are on disposable synthetic PDF fixtures, with an
explicit held acquisition lock. No provider I/O or real user data.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from cifereca.core import canonical_dedup_retirement as retirement
from cifereca.locking import InterProcessLock
from tests.test_core_p0_3171_atomic_publication_v14 import BYTES, rows
from tests.test_core_p0_3719_canonical_duplicate_retirement import rig, _retire


def _extra_profiles(storage, total=230):
    for n in range(total):
        storage.identity_put({
            "identity_id": f"unrelated-{n:04d}",
            "label": f"Unrelated {n}",
            "directory_name": f"unrelated-directory-{n:04d}",
        })


def _shadow(storage):
    # Cannot be constructed via normal V14 Storage due to UNIQUE index.
    with storage._lock, storage._connect() as db:
        db.execute("DROP INDEX uq_identity_directory_name")
    storage.identity_put({
        "identity_id": "shadow",
        "label": "Corrupt same-directory profile",
        "directory_name": "A",
    })


def _restore(storage):
    with storage._lock, storage._connect() as db:
        db.execute("DELETE FROM identities WHERE identity_id='shadow'")
        db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_identity_directory_name "
            "ON identities(directory_name)"
        )


def test_retirement_rehashes_under_exact_profile_with_many_unrelated_accounts(
    rig, monkeypatch,
):
    storage, _root, paths, publications, _snapshot, _lock = rig
    _extra_profiles(storage)
    inspected = []
    actual = retirement._verified_copy

    def copy(path, **kwargs):
        inspected.append(kwargs["registered_directories"])
        return actual(path, **kwargs)

    monkeypatch.setattr(retirement, "_verified_copy", copy)
    outcome = _retire(rig)
    assert outcome.retired_placement and outcome.removed_file
    assert not outcome.cleanup_pending
    assert len(inspected) >= 3
    assert all(only == frozenset({"A"}) for only in inspected)
    assert paths[0].read_bytes() == paths[2].read_bytes() == BYTES
    assert not paths[1].exists()
    assert storage.placement_get(publications[1].placement_id) is None


def test_external_shared_profile_root_blocks_retirement_before_any_unlink(rig):
    storage, _root, paths, publications, _snapshot, _lock = rig
    before = rows(storage)
    _shadow(storage)
    try:
        with pytest.raises(ValueError, match="canonical_retire_profile_ambiguous"):
            _retire(rig)
        assert rows(storage) == before
        assert all(p.is_file() for p in paths)
        assert storage.placement_get(publications[1].placement_id)["state"] == "present"
    finally:
        _restore(storage)


def test_pending_recovery_remains_blocked_on_shared_root_then_resumes(
    rig, monkeypatch,
):
    storage, root, paths, publications, _snapshot, lock_path = rig
    actual_unlink = Path.unlink

    def deny(path, *args, **kwargs):
        if path == paths[1]:
            raise PermissionError("synthetic_fs_denial")
        return actual_unlink(path, *args, **kwargs)

    with monkeypatch.context() as scoped:
        scoped.setattr(Path, "unlink", deny)
        first = _retire(rig)
    assert first.retired_placement and first.cleanup_pending
    assert storage.placement_get(publications[1].placement_id)[
        "state"
    ] == "dedup_cleanup_pending"
    before = rows(storage)
    _shadow(storage)
    try:
        with InterProcessLock(lock_path) as lock:
            blocked = retirement.recover_canonical_duplicate_retirement(
                storage, library_root=root,
                candidate_placement_id=publications[1].placement_id,
                keeper_placement_id=publications[0].placement_id,
                shared_lock=lock,
            )
        assert blocked.cleanup_pending and not blocked.removed_file
        assert rows(storage) == before
        assert paths[1].read_bytes() == BYTES
    finally:
        _restore(storage)
    with InterProcessLock(lock_path) as lock:
        recovered = retirement.recover_canonical_duplicate_retirement(
            storage, library_root=root,
            candidate_placement_id=publications[1].placement_id,
            keeper_placement_id=publications[0].placement_id,
            shared_lock=lock,
        )
    assert recovered.retired_placement and recovered.removed_file
    assert not recovered.cleanup_pending
    assert storage.placement_get(publications[1].placement_id) is None
    assert not paths[1].exists()
    assert paths[0].read_bytes() == BYTES
