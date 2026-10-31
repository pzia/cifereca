"""#3185: completed physical duplicate retirement resolves its own advisory pair.

SQLite and PDF fixtures are synthetic; no provider, GET or unconfirmed delete.
The shared lock and destructive confirmation remain exclusively Core-owned.
"""
from pathlib import Path

from cifereca.core.canonical_post_placement import (
    checkpoint_post_placement_duplicates_change,
    verified_post_placement_duplicates,
)
from cifereca.core.canonical_dedup_retirement import (
    recover_canonical_duplicate_retirement,
)
from cifereca.locking import InterProcessLock
from tests.test_core_p0_3719_canonical_duplicate_retirement import (
    _retire, rig,
)


def _pending_pair(rig):
    storage, root, _paths, publications, _snapshot, _lock_path = rig
    keeper, candidate, _foreign = publications
    matches = verified_post_placement_duplicates(
        storage, library_root=root, placement_id=keeper.placement_id,
    )
    assert matches == (candidate.placement_id,)
    key, changed = checkpoint_post_placement_duplicates_change(
        storage, placement_id=keeper.placement_id,
        duplicate_placement_ids=matches,
    )
    assert changed and storage.metadata_get(key)
    return key


def test_completed_retirement_resolves_only_exact_pair(rig):
    storage, _root, paths, publications, _snapshot, _lock_path = rig
    key = _pending_pair(rig)
    result = _retire(rig)
    assert result.retired_placement and result.removed_file
    assert result.cleanup_pending is False
    assert storage.metadata_get(key) is None
    assert storage.placement_get(publications[1].placement_id) is None
    assert storage.placement_get(publications[0].placement_id)["state"] == "present"
    assert storage.placement_get(publications[2].placement_id)["state"] == "present"
    assert paths[0].exists() and not paths[1].exists() and paths[2].exists()


def test_failed_unlink_does_not_acknowledge_checkpoint_until_explicit_recovery(
    rig, monkeypatch,
):
    storage, root, paths, publications, _snapshot, lock_path = rig
    key = _pending_pair(rig)
    original = Path.unlink

    def deny_copy(path, *args, **kwargs):
        if path == paths[1]:
            raise PermissionError("synthetic unlink refused")
        return original(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", deny_copy)
        outcome = _retire(rig)
    assert outcome.cleanup_pending and not outcome.removed_file
    assert storage.metadata_get(key) is not None
    assert storage.placement_get(publications[1].placement_id)["state"] == "dedup_cleanup_pending"
    with InterProcessLock(lock_path) as lock:
        recovered = recover_canonical_duplicate_retirement(
            storage, library_root=root,
            candidate_placement_id=publications[1].placement_id,
            keeper_placement_id=publications[0].placement_id,
            shared_lock=lock,
        )
    assert recovered.retired_placement and not recovered.cleanup_pending
    assert storage.metadata_get(key) is None
    assert not paths[1].exists() and paths[0].exists()


def test_unknown_checkpoint_is_not_deleted_as_part_of_safe_retirement(rig):
    storage, _root, paths, _publications, _snapshot, _lock = rig
    key = _pending_pair(rig)
    storage.metadata_set(key, '{"schema":"future-maintenance-format"}')
    retired = _retire(rig)
    assert retired.retired_placement and retired.removed_file
    assert retired.cleanup_pending is False
    assert storage.metadata_get(key) == '{"schema":"future-maintenance-format"}'
    assert paths[0].exists() and not paths[1].exists()
