"""#3719: backup path relocation is atomic, owner-bound and never unlinks."""
import sqlite3

import pytest

from cifereca.core.canonical_dedup_backup_relink import (
    relink_canonical_duplicate_backups,
)
from cifereca.locking import InterProcessLock
from tests.test_core_p0_3171_atomic_publication_v14 import BYTES, SHA, store
from tests.test_core_p0_3719_catalogue_relink import rig


def _add(storage, path, *, provider="seller-b", credential="card-b",
         account="borrower-b", identity="A", item="edition-2", digest=SHA):
    storage.backup_put({
        "provider_id": provider, "credential_id": credential,
        "item_id": item, "account_id": account, "identity_id": identity,
        "title": "Synthetic owned delivery", "status": "complete",
        "local_path": str(path), "sha256": digest, "size": len(BYTES),
    })


def _relink(rig):
    storage, root, _, (first, second, _foreign), lock_path = rig
    with InterProcessLock(lock_path) as lease:
        return relink_canonical_duplicate_backups(
            storage, library_root=root,
            candidate_placement_id=second.placement_id,
            keeper_placement_id=first.placement_id,
            shared_lock=lease,
        )


def test_backup_relink_is_idempotent_and_keeps_both_originals(rig):
    storage, _, paths, (first, second, foreign), _ = rig
    _add(storage, paths[1])
    before = storage.backup_list()
    assert len(before) == 1 and before[0]["local_path"] == str(paths[1])
    assert _relink(rig) == 1
    assert _relink(rig) == 0
    after = storage.backup_list()
    assert len(after) == 1
    assert after[0]["local_path"] == str(paths[0])
    assert all(after[0][field] == before[0][field] for field in
               ("provider_id", "credential_id", "account_id",
                "identity_id", "item_id", "sha256", "size", "status"))
    assert storage.placement_get(second.placement_id)["state"] == "present"
    assert storage.placement_get(foreign.placement_id)["identity_id"] == "B"
    assert all(p.read_bytes() == BYTES for p in paths)


@pytest.mark.parametrize("mode", ["wrong_sha", "foreign_owner", "unknown_account", "pending"])
def test_untrusted_backup_record_blocks_relink_without_partial_commit(rig, mode):
    storage, _, paths, (_first, second, _foreign), _ = rig
    props = {
        "wrong_sha": {"digest": "a" * 64},
        "foreign_owner": {"identity": "B"},
        "unknown_account": {"account": "other-borrower"},
        "pending": {},
    }
    _add(storage, paths[1], **props[mode])
    if mode == "pending":
        with storage._lock, storage._connect() as db:
            db.execute(
                "UPDATE backup_items SET status='attention_required' "
                "WHERE item_id='edition-2'"
            )
    with pytest.raises(ValueError, match="canonical_backup_relink_"):
        _relink(rig)
    assert storage.backup_list()[0]["local_path"] == str(paths[1])
    assert storage.placement_get(second.placement_id)["state"] == "present"
    assert all(p.read_bytes() == BYTES for p in paths)


def test_failed_second_update_rolls_back_first_backup_path(rig):
    storage, _, paths, (_first, _second, _foreign), _ = rig
    _add(storage, paths[1], item="edition-2")
    _add(storage, paths[1], item="edition-3")
    with storage._lock, storage._connect() as db:
        db.execute(
            "CREATE TRIGGER fail_relink_backup BEFORE UPDATE OF local_path "
            "ON backup_items WHEN NEW.item_id='edition-3' "
            "BEGIN SELECT RAISE(ABORT,'synthetic_backup_failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="synthetic_backup_failure"):
        _relink(rig)
    assert len(storage.backup_list()) == 2
    assert all(row["local_path"] == str(paths[1])
               for row in storage.backup_list())
    assert all(p.read_bytes() == BYTES for p in paths)


def test_relink_rejects_changed_keeper_file_and_unlocked_caller(rig):
    storage, root, paths, (first, second, _foreign), lock_path = rig
    _add(storage, paths[1])
    with pytest.raises(ValueError, match="canonical_backup_relink_invalid_request"):
        relink_canonical_duplicate_backups(
            storage, library_root=root,
            candidate_placement_id=second.placement_id,
            keeper_placement_id=first.placement_id,
            shared_lock=InterProcessLock(lock_path),
        )
    paths[0].write_bytes(b"unverified target")
    with pytest.raises(ValueError, match="canonical_backup_relink_physical_changed"):
        _relink(rig)
    assert storage.backup_list()[0]["local_path"] == str(paths[1])
    assert paths[1].read_bytes() == BYTES
