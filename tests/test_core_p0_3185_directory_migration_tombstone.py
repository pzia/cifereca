"""#3185: directory migrations must not destroy recoverable dedup tombstones.

Explicit SQLite directory-migration commit with synthetic files and profiles.
No provider, network, DRM or filesystem destructive operation is performed.
"""
import json

import pytest

from cifereca.identities.directory_migration import JOURNAL_KEY
from tests.test_core_p0_3171_atomic_publication_v14 import BYTES, rows, store
from tests.test_core_p0_3719_canonical_duplicate_retirement import rig


def pending(rig, *, evidence=None):
    storage, root, paths, publications, _snapshot, _lock = rig
    data = evidence if evidence is not None else {
        "core.canonical-dedup-retirement.v1": {
            "schema": "core.canonical-dedup-retirement.v1",
            "keeper_placement_id": publications[0].placement_id,
            "stat": {"dev": 1, "ino": 1, "size": len(BYTES),
                     "mtime_ns": 1, "ctime_ns": 1},
        }
    }
    raw = json.dumps(data, sort_keys=True)
    with storage._lock, storage._connect() as db:
        db.execute(
            "UPDATE placements SET state='dedup_cleanup_pending',metadata_json=? "
            "WHERE placement_id=?", (raw, publications[1].placement_id),
        )
    return raw


def plan(root, *, old, new, owner=None):
    if owner is None:
        return {
            "root": str(root), "entries": [{"old":str(old), "new":str(new)}],
            "changes": [],
        }
    return {
        "root": str(root), "entries": [],
        "changes": [{"old":str(old), "new":str(new), "identity_id": owner}],
    }


@pytest.mark.parametrize("case", ["candidate_path", "keeper_path", "profile_rename"])
def test_directory_migration_of_either_tombstone_path_fails_before_sql_write(
    rig, case,
):
    storage, root, paths, publications, _snapshot, _lock = rig
    raw = pending(rig)
    if case == "candidate_path":
        request = plan(root, old=paths[1], new=root / "A" / "moved-candidate.pdf")
    elif case == "keeper_path":
        request = plan(root, old=paths[0], new=root / "A" / "moved-keeper.pdf")
    else:
        request = plan(root, old=root / "A", new=root / "A-renamed", owner="A")
    before = rows(storage)
    with pytest.raises(ValueError, match="directory_migration_pending_dedup_"):
        storage.commit_directory_migration(request)
    assert storage.metadata_get(JOURNAL_KEY) is None
    with storage._lock, storage._connect() as db:
        evidence = db.execute(
            "SELECT path,state,metadata_json FROM placements WHERE placement_id=?",
            (publications[1].placement_id,),
        ).fetchone()
    assert evidence == (str(paths[1]), "dedup_cleanup_pending", raw)
    assert rows(storage) == before == (1, 3, 3, 3)
    assert all(path.read_bytes() == BYTES for path in paths)


@pytest.mark.parametrize("data", [
    {"core.canonical-dedup-retirement.v1": {}},
    {"core.canonical-dedup-retirement.v1": {"keeper_placement_id":"unknown"}},
])
def test_unverifiable_keeper_fails_closed_even_for_unrelated_migration(rig, data):
    storage, root, paths, publications, _snapshot, _lock = rig
    pending(rig, evidence=data)
    request = plan(root, old=paths[2], new=root / "B" / "moved-foreign.pdf")
    with pytest.raises(ValueError, match="directory_migration_pending_dedup_"):
        storage.commit_directory_migration(request)
    assert storage.metadata_get(JOURNAL_KEY) is None
    assert all(path.read_bytes() == BYTES for path in paths)


def test_unrelated_profile_can_migrate_without_erasing_tombstone(rig):
    storage, root, paths, publications, _snapshot, _lock = rig
    raw = pending(rig)
    other = root / "B" / "renamed-foreign.pdf"
    other.write_bytes(BYTES)  # Normally done by DirectoryMigration.run first.
    request = plan(root, old=paths[2], new=other)
    before = rows(storage)
    storage.commit_directory_migration(request)
    assert storage.placement_get(publications[2].placement_id)["path"] == str(other)
    with storage._lock, storage._connect() as db:
        evidence = db.execute(
            "SELECT path,state,metadata_json FROM placements WHERE placement_id=?",
            (publications[1].placement_id,),
        ).fetchone()
    assert evidence == (str(paths[1]), "dedup_cleanup_pending", raw)
    assert rows(storage) == before == (1, 3, 3, 3)
    assert storage.metadata_get(JOURNAL_KEY) is not None
    assert all(p.read_bytes() == BYTES for p in paths)
    assert other.read_bytes() == BYTES
