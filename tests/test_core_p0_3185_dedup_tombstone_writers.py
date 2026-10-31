"""#3185: pending physical dedup evidence cannot be clobbered by ordinary writers."""
import json

import pytest

from tests.test_core_p0_3171_atomic_publication_v14 import (
    BYTES, new_source, publish, rows, store,
)
from tests.test_core_p0_3719_canonical_duplicate_retirement import rig


def _mark_pending(rig):
    storage, _root, paths, pubs, _snapshot, _lock = rig
    evidence = {
        "core.canonical-dedup-retirement.v1": {
            "schema": "core.canonical-dedup-retirement.v1",
            "keeper_placement_id": pubs[0].placement_id,
            "stat": {"dev": 1, "ino": 1, "size": len(BYTES),
                     "mtime_ns": 1, "ctime_ns": 1},
        }
    }
    raw = json.dumps(evidence, sort_keys=True)
    with storage._lock, storage._connect() as db:
        db.execute(
            "UPDATE placements SET state='dedup_cleanup_pending',metadata_json=? "
            "WHERE placement_id=?", (raw, pubs[1].placement_id),
        )
    return raw


def test_plain_placement_upsert_cannot_resurrect_tombstone(rig):
    storage, _root, paths, pubs, _snapshot, _lock = rig
    raw = _mark_pending(rig)
    before = rows(storage)
    with pytest.raises(ValueError, match="dedup_cleanup_pending"):
        storage.placement_put({
            "path": str(paths[1]), "artifact_id": pubs[1].artifact_id,
            "identity_id": "A", "state": "present",
            "metadata": {"overwrite": "never"},
        })
    with storage._lock, storage._connect() as db:
        assert db.execute(
            "SELECT state,metadata_json FROM placements WHERE placement_id=?",
            (pubs[1].placement_id,),
        ).fetchone() == ("dedup_cleanup_pending", raw)
    assert rows(storage) == before
    assert all(p.read_bytes() == BYTES for p in paths)


def test_canonical_publisher_cannot_rebind_pending_inode_even_with_same_sha(rig):
    storage, root, paths, pubs, _snapshot, _lock = rig
    raw = _mark_pending(rig)
    before = rows(storage)
    source, snapshot = new_source(
        "A", provider="seller-b", credential="card-b", account="buyer-b",
    )
    with pytest.raises(ValueError, match="canonical_pending_duplicate_cleanup"):
        publish(storage, root, paths[1], source, snapshot)
    with storage._lock, storage._connect() as db:
        assert db.execute(
            "SELECT state,metadata_json FROM placements WHERE placement_id=?",
            (pubs[1].placement_id,),
        ).fetchone() == ("dedup_cleanup_pending", raw)
    assert rows(storage) == before
    assert all(p.read_bytes() == BYTES for p in paths)


def test_other_unrelated_placement_can_be_updated_while_tombstone_pending(rig):
    storage, _root, paths, pubs, _snapshot, _lock = rig
    _mark_pending(rig)
    own = storage.placement_put({
        "path": str(paths[0]), "artifact_id": pubs[0].artifact_id,
        "identity_id": "A", "state": "present", "metadata": {},
    })
    assert own == pubs[0].placement_id
    assert storage.placement_get(pubs[1].placement_id)["state"] == "dedup_cleanup_pending"
    assert all(p.read_bytes() == BYTES for p in paths)
