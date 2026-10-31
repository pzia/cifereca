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


def test_plain_placement_delete_cannot_erase_recovery_evidence(rig):
    storage, _root, paths, pubs, _snapshot, _lock = rig
    raw = _mark_pending(rig)
    before = rows(storage)
    with pytest.raises(ValueError, match="dedup_cleanup_pending"):
        storage.placement_delete(pubs[1].placement_id)
    with storage._lock, storage._connect() as db:
        assert db.execute(
            "SELECT state,metadata_json FROM placements WHERE placement_id=?",
            (pubs[1].placement_id,),
        ).fetchone() == ("dedup_cleanup_pending", raw)
    assert rows(storage) == before
    assert paths[1].read_bytes() == BYTES


def test_missing_scan_must_not_turn_pending_tombstone_into_missing(rig):
    storage, root, paths, pubs, _snapshot, _lock = rig
    raw = _mark_pending(rig)
    found = storage.placements_mark_missing(
        root, {str(paths[0]), str(paths[2])},
        candidate_placement_ids={pubs[1].placement_id},
    )
    assert found == 0
    with storage._lock, storage._connect() as db:
        assert db.execute(
            "SELECT state,metadata_json FROM placements WHERE placement_id=?",
            (pubs[1].placement_id,),
        ).fetchone() == ("dedup_cleanup_pending", raw)
    assert paths[1].read_bytes() == BYTES


def test_scan_cas_rejects_tombstone_committed_after_snapshot(rig, monkeypatch):
    """Reproduce a real independent SQLite writer between SELECT and UPDATE."""
    import sqlite3

    storage, root, paths, pubs, _snapshot, _lock = rig
    original_connect = storage._connect
    marker = json.dumps({
        "core.canonical-dedup-retirement.v1": {
            "schema": "core.canonical-dedup-retirement.v1",
            "keeper_placement_id": pubs[0].placement_id,
            "stat": {"dev": 1, "ino": 1, "size": len(BYTES),
                     "mtime_ns": 1, "ctime_ns": 1},
        }
    }, sort_keys=True)
    fired = []

    class RacedConnection:
        def __init__(self):
            self.conn = original_connect()

        def __enter__(self):
            self.conn.__enter__()
            return self

        def __exit__(self, *args):
            return self.conn.__exit__(*args)

        def execute(self, sql, params=()):
            if sql == "SELECT placement_id,path,state FROM placements":
                cursor = self.conn.execute(sql, params)
                stale_rows = cursor.fetchall()
                cursor.close()
                if not fired:
                    with sqlite3.connect(storage.path) as writer:
                        writer.execute(
                            "UPDATE placements SET state='dedup_cleanup_pending',"
                            "metadata_json=? WHERE placement_id=?",
                            (marker, pubs[1].placement_id),
                        )
                    fired.append(True)

                class FrozenRows:
                    def fetchall(self):
                        return stale_rows
                return FrozenRows()
            return self.conn.execute(sql, params)

    with monkeypatch.context() as patch:
        patch.setattr(storage, "_connect", RacedConnection)
        marked = storage.placements_mark_missing(
            root, {str(paths[0]), str(paths[2])},
            candidate_placement_ids={pubs[1].placement_id},
        )

    assert fired == [True]
    assert marked == 0
    with storage._lock, original_connect() as db:
        row = db.execute(
            "SELECT path,state,metadata_json FROM placements "
            "WHERE placement_id=?", (pubs[1].placement_id,),
        ).fetchone()
    assert row == (str(paths[1]), "dedup_cleanup_pending", marker)
    assert all(path.read_bytes() == BYTES for path in paths)
