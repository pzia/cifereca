"""#3185: read-only recovery queue never infers permission from a pending row."""
import json
from pathlib import Path

import pytest

from cifereca.core.canonical_dedup_pending_review import (
    review_pending_canonical_cleanups,
)
from cifereca.core.canonical_dedup_retirement import (
    retire_verified_canonical_duplicate,
)
from cifereca.locking import InterProcessLock
from tests.test_core_p0_3171_atomic_publication_v14 import BYTES, rows, store
from tests.test_core_p0_3719_canonical_duplicate_retirement import rig


@pytest.fixture
def pending_rig(rig, monkeypatch):
    storage, root, paths, pubs, _snapshot, lock_path = rig
    unlink_original = Path.unlink

    def deny_duplicate_unlink(path, *args, **kwargs):
        if path == paths[1]:
            raise PermissionError("synthetic_fs_refusal")
        return unlink_original(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", deny_duplicate_unlink)
        with InterProcessLock(lock_path) as lock:
            outcome = retire_verified_canonical_duplicate(
                storage, library_root=root,
                candidate_placement_id=pubs[1].placement_id,
                keeper_placement_id=pubs[0].placement_id,
                shared_lock=lock,
            )
    assert outcome.retired_placement and outcome.cleanup_pending
    assert not outcome.removed_file
    assert storage.placement_get(pubs[1].placement_id)["state"] == "dedup_cleanup_pending"
    return rig


def test_readonly_review_returns_only_matching_profile_pair(pending_rig):
    storage, _root, paths, pubs, _snapshot, _lock = pending_rig
    initial = rows(storage)
    before = storage.placement_get(pubs[1].placement_id)
    found = review_pending_canonical_cleanups(storage)
    assert found.status == "reviewed"
    assert found.unverified_count == 0
    assert found.recovery_candidates == 1
    assert found.candidates[0].candidate_placement_id == pubs[1].placement_id
    assert found.candidates[0].keeper_placement_id == pubs[0].placement_id
    assert found.candidates[0].identity_id == "A"
    assert review_pending_canonical_cleanups(
        storage, identity_id="A",
    ) == found
    assert review_pending_canonical_cleanups(
        storage, identity_id="B",
    ).candidates == ()
    assert rows(storage) == initial == (1, 3, 3, 3)
    assert storage.placement_get(pubs[1].placement_id) == before
    assert all(path.read_bytes() == BYTES for path in paths)


@pytest.mark.parametrize("evidence", [
    {}, {"core.canonical-dedup-retirement.v1": {}},
    {"core.canonical-dedup-retirement.v1": {
        "schema": "core.canonical-dedup-retirement.v1",
        "keeper_placement_id": "unknown-placement",
        "stat": {"dev": 1, "ino": 1, "size": len(BYTES),
                 "mtime_ns": 1, "ctime_ns": 1},
    }},
    {"core.canonical-dedup-retirement.v1": {
        "schema": "core.canonical-dedup-retirement.v1",
        "keeper_placement_id": True,
        "stat": {"dev": 1, "ino": 1, "size": len(BYTES),
                 "mtime_ns": 1, "ctime_ns": 1},
    }},
])
def test_unknown_or_malformed_tombstone_cannot_create_recovery_candidate(
    pending_rig, evidence,
):
    storage, _root, paths, pubs, _snapshot, _lock = pending_rig
    raw = json.dumps(evidence, sort_keys=True)
    with storage._lock, storage._connect() as db:
        db.execute(
            "UPDATE placements SET metadata_json=? WHERE placement_id=?",
            (raw, pubs[1].placement_id),
        )
    found = review_pending_canonical_cleanups(storage)
    assert found.status == "partial"
    assert found.unverified_count == 1
    assert found.recovery_candidates == 0
    assert storage.placement_get(pubs[1].placement_id)["state"] == "dedup_cleanup_pending"
    assert all(path.read_bytes() == BYTES for path in paths)


def test_bounded_sqlite_metadata_never_escapes_into_public_review(pending_rig):
    storage, _root, paths, pubs, _snapshot, _lock = pending_rig
    raw = json.dumps({"untrusted": "SECRET_TOKEN" * 7000})
    assert len(raw) > 65536
    with storage._lock, storage._connect() as db:
        db.execute(
            "UPDATE placements SET metadata_json=? WHERE placement_id=?",
            (raw, pubs[1].placement_id),
        )
    result = review_pending_canonical_cleanups(storage)
    assert result.status == "partial"
    assert result.unverified_count == 1
    assert result.candidates == ()
    assert "SECRET_TOKEN" not in str(result)
    assert all(path.read_bytes() == BYTES for path in paths)


def test_same_sha_foreign_keeper_is_never_an_advisory_recovery_candidate(pending_rig):
    storage, _root, paths, pubs, _snapshot, _lock = pending_rig
    with storage._lock, storage._connect() as db:
        row = db.execute(
            "SELECT metadata_json FROM placements WHERE placement_id=?",
            (pubs[1].placement_id,),
        ).fetchone()
        payload = json.loads(row[0])
        payload["core.canonical-dedup-retirement.v1"]["keeper_placement_id"] = pubs[2].placement_id
        db.execute(
            "UPDATE placements SET metadata_json=? WHERE placement_id=?",
            (json.dumps(payload), pubs[1].placement_id),
        )
    result = review_pending_canonical_cleanups(storage)
    assert (result.status, result.recovery_candidates, result.unverified_count) == (
        "partial", 0, 1,
    )
    assert all(path.read_bytes() == BYTES for path in paths)


def test_missing_keeper_and_invalid_identity_scope_fail_closed(pending_rig):
    storage, _root, paths, pubs, _snapshot, _lock = pending_rig
    with storage._lock, storage._connect() as db:
        db.execute(
            "DELETE FROM placements WHERE placement_id=?",
            (pubs[0].placement_id,),
        )
    assert review_pending_canonical_cleanups(storage).status == "partial"
    assert review_pending_canonical_cleanups(storage).candidates == ()
    for identity in ("A\n", "", True, "x" * 257):
        found = review_pending_canonical_cleanups(storage, identity_id=identity)
        assert found.status == "unavailable" and not found.candidates
    assert all(path.read_bytes() == BYTES for path in paths)


def test_pending_queue_over_budget_returns_no_unverified_pair_ids(pending_rig):
    storage, root, paths, pubs, _snapshot, _lock = pending_rig
    with storage._lock, storage._connect() as db:
        db.executemany(
            "INSERT INTO placements(placement_id,artifact_id,path,identity_id,"
            "state,metadata_json,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            [
                (f"synthetic-pending-{n}", pubs[1].artifact_id,
                 str(root / "A" / f"synthetic-extra-{n}.pdf"), "A",
                 "dedup_cleanup_pending", "{}", 1, 1)
                for n in range(1000)
            ],
        )
    reviewed = review_pending_canonical_cleanups(storage)
    assert reviewed.status == "over_budget"
    assert reviewed.candidates == ()
    assert reviewed.unverified_count == 0
    assert all(path.read_bytes() == BYTES for path in paths)
