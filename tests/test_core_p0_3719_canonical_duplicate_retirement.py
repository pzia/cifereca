"""#3719/#3185: physical canonical cleanup occurs only after reference-safe SQL.

Real SQLite and synthetic PDF bytes, no supplier or DRM access.
"""
from pathlib import Path
import json
import sqlite3

import pytest

from cifereca.core.canonical_dedup_retirement import (
    retire_verified_canonical_duplicate,
)
from cifereca.core.convergence_verified_presence import (
    plan_existing_verified_presence,
)
from cifereca.locking import InterProcessLock
from tests.test_core_p0_3171_atomic_publication_v14 import (
    BYTES, new_source, publish, register, rows, store, target,
)


@pytest.fixture
def rig(store, tmp_path, monkeypatch):
    root = tmp_path / "EBooks"
    first, first_snapshot = new_source(
        "A", provider="seller-a", credential="card-a", account="buyer-a",
    )
    second, second_snapshot = new_source(
        "A", provider="seller-b", credential="card-b", account="buyer-b",
    )
    foreign, foreign_snapshot = new_source(
        "B", provider="seller-c", credential="card-c", account="buyer-c",
    )
    for profile, provider, credential, account in (
        ("A", "seller-a", "card-a", "buyer-a"),
        ("A", "seller-b", "card-b", "buyer-b"),
        ("B", "seller-c", "card-c", "buyer-c"),
    ):
        register(store, profile, provider, credential, account=account)
    paths = (
        target(tmp_path, "A", "first.pdf"),
        target(tmp_path, "A", "second.pdf"),
        target(tmp_path, "B", "foreign.pdf"),
    )
    publications = (
        publish(store, root, paths[0], first, first_snapshot),
        publish(store, root, paths[1], second, second_snapshot),
        publish(store, root, paths[2], foreign, foreign_snapshot),
    )
    lock_path = tmp_path / "locks" / "acquisition.lock"
    monkeypatch.setattr(
        "cifereca.core.canonical_recovery.acquisition_lock_file",
        lambda: lock_path,
    )
    return store, root, paths, publications, second_snapshot, lock_path


def _retire(rig, *, candidate=None, keeper=None):
    storage, root, _paths, publications, _snapshot, lock_path = rig
    with InterProcessLock(lock_path) as lock:
        return retire_verified_canonical_duplicate(
            storage, library_root=root,
            candidate_placement_id=candidate or publications[1].placement_id,
            keeper_placement_id=keeper or publications[0].placement_id,
            shared_lock=lock,
        )


def test_relinked_unreferenced_canonical_copy_can_retire_without_losing_origins(rig):
    storage, root, paths, publications, snapshot_b, _lock = rig
    assert rows(storage) == (1, 3, 3, 3)
    result = _retire(rig)
    assert result.retired_placement and result.removed_file
    assert not result.cleanup_pending
    assert paths[0].read_bytes() == paths[2].read_bytes() == BYTES
    assert not paths[1].exists()
    assert storage.placement_get(publications[1].placement_id) is None
    assert storage.placement_get(publications[0].placement_id)["state"] == "present"
    assert rows(storage) == (1, 2, 3, 3)

    # Both provider OriginLinks still bind to a real same-profile verified
    # Artifact even though their former copies are no longer both on disk.
    plan = plan_existing_verified_presence(storage, snapshot_b, library_root=root)
    assert len(plan.decisions) == 1
    assert plan.decisions[0].provider_satisfied


@pytest.mark.parametrize("dependency", ["catalogue", "checkpoint", "metadata"])
def test_independent_active_path_reference_blocks_retirement(rig, dependency):
    storage, _root, paths, publications, _snapshot, _lock = rig
    if dependency == "catalogue":
        storage.catalog_entry_put({
            "catalog_entry_id": "still-attached",
            "artifact_id": publications[1].artifact_id,
            "placement_id": publications[1].placement_id,
            "identity_id": "A", "provider_id": "seller-b",
            "item_id": "book-b", "title": "synthetic",
            "metadata": {"path": str(paths[1])},
        })
    elif dependency == "checkpoint":
        # Unknown in-flight receipts are not a licence to relink or delete.
        with storage._lock, storage._connect() as db:
            db.execute(
                "INSERT INTO acquisition_ledger(acquisition_id,provider_id,"
                "credential_id,account_id,item_id,identity_id,status,"
                "metadata_json,created_at,updated_at)"
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                ("in-progress", "seller-b", "card-b", "buyer-b",
                 "edition", "A", "canonical_receipt_staged",
                 json.dumps({"path": str(paths[1])}), 1, 1),
            )
    else:
        storage.metadata_set("test.legacy.operational.path", str(paths[1]))
    with pytest.raises(ValueError, match="canonical_retire_active_references"):
        _retire(rig)
    assert all(path.read_bytes() == BYTES for path in paths)
    assert rows(storage)[:2] == (1, 3)


def test_foreign_profile_or_tampered_bytes_refuses_retirement(rig):
    storage, _root, paths, publications, _snapshot, _lock = rig
    with pytest.raises(ValueError, match="canonical_retire_scope_changed"):
        _retire(rig, keeper=publications[2].placement_id)
    paths[1].write_bytes(BYTES[:-1] + b"X")
    with pytest.raises(ValueError, match="canonical_retire_bytes_changed"):
        _retire(rig)
    assert storage.placement_get(publications[1].placement_id) is not None
    assert all(path.exists() for path in paths)


def test_sql_failure_never_precedes_physical_file_loss(rig):
    storage, _root, paths, publications, _snapshot, _lock = rig
    with storage._lock, storage._connect() as db:
        db.execute(
            "CREATE TRIGGER refuse_retirement BEFORE UPDATE OF state ON placements "
            "WHEN NEW.state='dedup_cleanup_pending' "
            "BEGIN SELECT RAISE(ABORT,'synthetic_retire_failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="synthetic_retire_failure"):
        _retire(rig)
    assert storage.placement_get(publications[1].placement_id) is not None
    assert all(path.read_bytes() == BYTES for path in paths)


def test_post_commit_unlink_denial_keeps_extra_recoverable_copy(rig, monkeypatch):
    storage, _root, paths, publications, _snapshot, _lock = rig
    original_unlink = Path.unlink

    def deny(path, *args, **kwargs):
        if path == paths[1]:
            raise PermissionError("synthetic_fs_denial")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", deny)
    result = _retire(rig)
    assert result.retired_placement
    assert not result.removed_file and result.cleanup_pending
    assert all(path.read_bytes() == BYTES for path in paths)
    assert storage.placement_get(publications[1].placement_id)["state"] == "dedup_cleanup_pending"
    assert storage.placement_get(publications[0].placement_id)["state"] == "present"


def test_requires_real_held_lock(rig):
    storage, root, paths, publications, _snapshot, lock_path = rig
    with pytest.raises(ValueError, match="canonical_retire_invalid_request"):
        retire_verified_canonical_duplicate(
            storage, library_root=root,
            candidate_placement_id=publications[1].placement_id,
            keeper_placement_id=publications[0].placement_id,
            shared_lock=InterProcessLock(lock_path),
        )
    assert all(path.exists() for path in paths)
