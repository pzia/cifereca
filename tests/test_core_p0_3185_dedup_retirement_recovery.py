"""#3185: canonical duplicate tombstones survive filesystem/SQLite crash gaps."""
import json
import sqlite3
from pathlib import Path
import hashlib

from cifereca.core.canonical_publication import publish_completed_acquisition
from tests.test_core_p0_3171_atomic_publication_v14 import new_source

import pytest

from cifereca.core.canonical_dedup_retirement import (
    recover_canonical_duplicate_retirement,
    retire_verified_canonical_duplicate,
)
from cifereca.locking import InterProcessLock
from tests.test_core_p0_3719_canonical_duplicate_retirement import rig
from tests.test_core_p0_3171_atomic_publication_v14 import BYTES, rows, store


def _run_recovery(rig):
    storage, root, _paths, pubs, _snapshot, lock_path = rig
    with InterProcessLock(lock_path) as lock:
        return recover_canonical_duplicate_retirement(
            storage, library_root=root,
            candidate_placement_id=pubs[1].placement_id,
            keeper_placement_id=pubs[0].placement_id,
            shared_lock=lock,
        )


def _stage_pending(rig, monkeypatch):
    storage, root, paths, pubs, _snapshot, lock_path = rig
    original = Path.unlink

    def reject(path, *args, **kwargs):
        if path == paths[1]:
            raise PermissionError("synthetic_fs_failure")
        return original(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", reject)
        with InterProcessLock(lock_path) as lock:
            result = retire_verified_canonical_duplicate(
                storage, library_root=root,
                candidate_placement_id=pubs[1].placement_id,
                keeper_placement_id=pubs[0].placement_id,
                shared_lock=lock,
            )
    assert result.retired_placement and result.cleanup_pending
    assert not result.removed_file
    assert storage.placement_get(pubs[1].placement_id)["state"] == "dedup_cleanup_pending"
    assert paths[1].read_bytes() == BYTES
    return result


def test_recovery_after_unlink_denial_revalidates_and_finishes(rig, monkeypatch):
    storage, _root, paths, pubs, _snapshot, lock_path = rig
    before = rows(storage)
    _stage_pending(rig, monkeypatch)
    with storage._lock, storage._connect() as db:
        raw = db.execute(
            "SELECT metadata_json FROM placements WHERE placement_id=?",
            (pubs[1].placement_id,),
        ).fetchone()[0]
        tombstone = json.loads(raw)["core.canonical-dedup-retirement.v1"]
    assert tombstone["keeper_placement_id"] == pubs[0].placement_id
    assert set(tombstone["stat"]) == {
        "dev", "ino", "size", "mtime_ns", "ctime_ns",
    }
    result = _run_recovery(rig)
    assert result.retired_placement and result.removed_file
    assert not result.cleanup_pending
    assert not paths[1].exists()
    assert paths[0].read_bytes() == paths[2].read_bytes() == BYTES
    assert storage.placement_get(pubs[1].placement_id) is None
    assert rows(storage) == (1, 2, 3, 3)
    assert _run_recovery(rig).cleanup_pending is False


@pytest.mark.parametrize("changed", ["new_bytes", "symlink", "foreign_keeper"])
def test_recovery_refuses_stale_inode_or_wrong_keeper(rig, monkeypatch, changed):
    storage, _root, paths, pubs, _snapshot, _lock = rig
    _stage_pending(rig, monkeypatch)
    if changed == "new_bytes":
        paths[1].write_bytes(BYTES[:-1] + b"X")
    elif changed == "symlink":
        paths[1].unlink()
        paths[1].symlink_to(paths[0])
    else:
        paths[0].write_bytes(b"tampered keeper")
    result = _run_recovery(rig)
    assert result.cleanup_pending
    assert not result.removed_file
    assert paths[1].exists()
    assert storage.placement_get(pubs[1].placement_id)["state"] == "dedup_cleanup_pending"


def test_missing_copy_after_interruption_is_not_an_active_placement(rig, monkeypatch):
    storage, _root, paths, pubs, _snapshot, _lock = rig
    _stage_pending(rig, monkeypatch)
    paths[1].unlink()
    assert not paths[1].exists()
    result = _run_recovery(rig)
    assert result.retired_placement and not result.removed_file
    assert not result.cleanup_pending
    assert storage.placement_get(pubs[1].placement_id) is None


def test_sql_failure_after_unlink_retains_recovery_tombstone(rig):
    storage, root, paths, pubs, _snapshot, lock_path = rig
    with storage._lock, storage._connect() as db:
        db.execute(
            "CREATE TRIGGER block_retired_state "
            "BEFORE DELETE ON placements "
            "WHEN OLD.state='dedup_cleanup_pending' "
            "BEGIN SELECT RAISE(ABORT,'synthetic_post_unlink_failure'); END"
        )
    with InterProcessLock(lock_path) as lock:
        result = retire_verified_canonical_duplicate(
            storage, library_root=root,
            candidate_placement_id=pubs[1].placement_id,
            keeper_placement_id=pubs[0].placement_id,
            shared_lock=lock,
        )
    assert result.retired_placement and result.removed_file
    assert result.cleanup_pending
    assert not paths[1].exists()
    assert storage.placement_get(pubs[1].placement_id)["state"] == "dedup_cleanup_pending"
    with storage._lock, storage._connect() as db:
        db.execute("DROP TRIGGER block_retired_state")
    resolved = _run_recovery(rig)
    assert resolved.retired_placement and not resolved.removed_file
    assert not resolved.cleanup_pending
    assert storage.placement_get(pubs[1].placement_id) is None


def test_new_active_dependency_during_recovery_blocks_unlink(rig, monkeypatch):
    storage, _root, paths, pubs, _snapshot, _lock = rig
    _stage_pending(rig, monkeypatch)
    storage.metadata_set("test.new.active-reference", str(paths[1]))
    result = _run_recovery(rig)
    assert result.cleanup_pending and not result.removed_file
    assert paths[1].read_bytes() == BYTES
    assert storage.placement_get(pubs[1].placement_id)["state"] == "dedup_cleanup_pending"


def test_recovery_requires_live_lock_and_never_follows_rebound_path(rig, monkeypatch):
    storage, root, paths, pubs, _snapshot, lock_path = rig
    _stage_pending(rig, monkeypatch)
    with pytest.raises(ValueError, match="canonical_recover_invalid_request"):
        recover_canonical_duplicate_retirement(
            storage, library_root=root,
            candidate_placement_id=pubs[1].placement_id,
            keeper_placement_id=pubs[0].placement_id,
            shared_lock=InterProcessLock(lock_path),
        )
    assert paths[1].read_bytes() == BYTES


def test_foreign_profile_tombstone_cannot_be_recovered(rig, monkeypatch):
    storage, root, paths, pubs, _snapshot, lock_path = rig
    _stage_pending(rig, monkeypatch)
    with InterProcessLock(lock_path) as lock:
        result = recover_canonical_duplicate_retirement(
            storage, library_root=root,
            candidate_placement_id=pubs[1].placement_id,
            keeper_placement_id=pubs[2].placement_id,
            shared_lock=lock,
        )
    assert result.cleanup_pending and not result.removed_file
    assert paths[1].read_bytes() == BYTES


def test_successful_cleanup_releases_path_for_new_unrelated_book(rig):
    storage, root, paths, pubs, _snapshot, lock_path = rig
    original_artifact = pubs[1].artifact_id
    with InterProcessLock(lock_path) as lock:
        first = retire_verified_canonical_duplicate(
            storage, library_root=root,
            candidate_placement_id=pubs[1].placement_id,
            keeper_placement_id=pubs[0].placement_id,
            shared_lock=lock,
        )
    assert first.retired_placement and first.removed_file
    assert not first.cleanup_pending
    assert storage.placement_get(pubs[1].placement_id) is None
    assert not paths[1].exists()
    # The former path is not a permanent UNIQUE tombstone. Publishing a
    # genuinely different source/artifact there must remain supported.
    new_bytes = BYTES + b"\nSynthetic new independent file\n"
    paths[1].write_bytes(new_bytes)
    source, snapshot = new_source(
        "A", provider="seller-b", credential="card-b",
        account="buyer-b", edition="new-independent-book",
    )
    new_sha = hashlib.sha256(new_bytes).hexdigest()
    published = publish_completed_acquisition(
        storage, library_root=root, path=paths[1], source=source,
        snapshot=snapshot, final_sha256=new_sha,
        delivery_receipt_ref="new-synthetic-delivery-receipt",
    )
    assert published.artifact_id != original_artifact
    assert storage.placement_get(published.placement_id)["state"] == "present"
    assert paths[1].read_bytes() == new_bytes
    assert paths[0].read_bytes() == BYTES


def test_directory_fsync_failure_preserves_tombstone_for_later_recovery(
    rig, monkeypatch,
):
    import os

    storage, root, paths, pubs, _snapshot, lock_path = rig
    actual_fsync = os.fsync
    attempted = []

    def fail_fsync(fd):
        attempted.append(fd)
        raise OSError("synthetic_parent_directory_fsync_denied")

    with monkeypatch.context() as patch:
        patch.setattr(os, "fsync", fail_fsync)
        with InterProcessLock(lock_path) as lock:
            failed = retire_verified_canonical_duplicate(
                storage, library_root=root,
                candidate_placement_id=pubs[1].placement_id,
                keeper_placement_id=pubs[0].placement_id,
                shared_lock=lock,
            )
    assert attempted
    assert failed.retired_placement and failed.removed_file
    assert failed.cleanup_pending
    assert not paths[1].exists()
    assert storage.placement_get(pubs[1].placement_id)["state"] == "dedup_cleanup_pending"
    assert paths[0].read_bytes() == paths[2].read_bytes() == BYTES

    fsynced = []
    def successful_fsync(fd):
        fsynced.append(fd)
        return actual_fsync(fd)

    with monkeypatch.context() as patch:
        patch.setattr(os, "fsync", successful_fsync)
        recovered = _run_recovery(rig)
    assert fsynced
    assert recovered.retired_placement and not recovered.removed_file
    assert not recovered.cleanup_pending
    assert storage.placement_get(pubs[1].placement_id) is None
    assert paths[0].read_bytes() == paths[2].read_bytes() == BYTES


def test_directory_fsync_is_invoked_after_unlink_before_finalization(
    rig, monkeypatch,
):
    import os

    storage, root, paths, pubs, _snapshot, lock_path = rig
    actual_fsync = os.fsync
    states = []

    def inspect_fsync(fd):
        states.append((not paths[1].exists(), os.fstat(fd).st_mode))
        return actual_fsync(fd)

    with monkeypatch.context() as patch:
        patch.setattr(os, "fsync", inspect_fsync)
        with InterProcessLock(lock_path) as lock:
            finished = retire_verified_canonical_duplicate(
                storage, library_root=root,
                candidate_placement_id=pubs[1].placement_id,
                keeper_placement_id=pubs[0].placement_id,
                shared_lock=lock,
            )
    assert finished.retired_placement and finished.removed_file
    assert not finished.cleanup_pending
    assert states and states[0][0]
    assert storage.placement_get(pubs[1].placement_id) is None
