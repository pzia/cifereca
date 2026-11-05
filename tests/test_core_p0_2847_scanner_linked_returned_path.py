"""#2847: scanned linked Placement may be restored when bytes return exactly.

The scanner writes a nonempty file envelope. Its historical mtime is not
proof of ownership; the original path, Artifact SHA, profile, linked catalogue
and current inode remain independently reverified at preview and commit.
All files, identities and SQLite rows here are synthetic.
"""
from __future__ import annotations

import json

import pytest

from cifereca.core.missing_placement_linked_same_path import (
    preview_reappeared_linked_placement,
    restore_reappeared_linked_placement,
)
from cifereca.core.missing_placement_same_path import (
    preview_reappeared_missing_placement,
)
from cifereca.locking import InterProcessLock
from tests.test_core_p0_2847_relocation_transaction import rig, catalogue


def _scan_envelope(rig, **changes):
    db, root, old, new, pid, artifact, payload, lockpath = rig
    meta = {
        "relative_path": str(old.relative_to(root)),
        "artifact_kind": "file", "file_size": len(payload),
        "mtime_ns": 123456789,
    }
    meta.update(changes)
    with db._lock, db._connect() as sql:
        sql.execute(
            "UPDATE placements SET metadata_json=? WHERE placement_id=?",
            (json.dumps(meta, sort_keys=True), pid),
        )


def _preview(rig):
    db, root, old, new, pid, artifact, payload, lockpath = rig
    return preview_reappeared_linked_placement(
        db, library_root=root, identity_id="alice", placement_id=pid,
    )


def _commit(rig, token):
    db, root, old, new, pid, artifact, payload, lockpath = rig
    with InterProcessLock(lockpath) as locked:
        return restore_reappeared_linked_placement(
            db, library_root=root, identity_id="alice", placement_id=pid,
            plan_token=token, confirmed=True, shared_lock=locked,
        )


def test_scanner_linked_file_missing_then_returned_can_be_restored(rig):
    db, root, old, new, pid, artifact, payload, lockpath = rig
    catalogue(rig)
    _scan_envelope(rig)
    # Follow the same Placement/catalogue status transition used by a scan.
    with db._lock, db._connect() as sql:
        sql.execute(
            "UPDATE placements SET state='present' WHERE placement_id=?", (pid,)
        )
        sql.execute(
            "UPDATE catalog_entries SET local_state='local' "
            "WHERE catalog_entry_id='catalogue:original'"
        )
    assert db.placements_mark_missing(
        root, set(), candidate_placement_ids={pid},
    ) == 1
    assert db.placement_get(pid)["state"] == "missing"
    assert db.catalog_entry_get("catalogue:original")["local_state"] == "missing"
    old.write_bytes(payload)
    before = db.placement_get(pid)
    prior_catalogue = db.catalog_entry_get("catalogue:original")
    assert preview_reappeared_missing_placement(
        db, library_root=root, identity_id="alice", placement_id=pid,
    )["reason"] == "linked_catalogue_requires_review"
    prepared = _preview(rig)
    assert prepared["status"] == "ready"
    assert prepared["reason"] == "linked_catalogue_reappeared"
    assert prepared["catalogue_rows"] == 1
    assert _commit(rig, prepared["plan_token"]) == {
        "status": "restored", "reason": "linked_catalogue_reappeared",
        "catalogue_rows": 1,
    }
    updated = db.placement_get(pid)
    assert updated["state"] == "present"
    assert updated["metadata"] == before["metadata"]
    assert db.catalog_entry_get("catalogue:original")["metadata"] == prior_catalogue["metadata"]
    assert db.catalog_entry_get("catalogue:original")["local_state"] == "local"
    assert old.read_bytes() == payload
    assert db.entitlements() == [] and db.acquisitions() == []
    assert _commit(rig, prepared["plan_token"])["reason"] == "not_missing"


@pytest.mark.parametrize("change", [
    {"relative_path": "bob/kobo/old.epub"},
    {"relative_path": "../alice/kobo/old.epub"},
    {"artifact_kind": "bundle"},
    {"artifact_kind": "remote"},
    {"file_size": 1234567},
    {"file_size": True},
    {"mtime_ns": False},
    {"mtime_ns": "historical"},
    {"mtime_ns": -1},
    {"source_reference_id": "borrower-foreign"},
    {"relative_path": ""},
])
def test_only_exact_scanner_envelope_is_admitted(rig, change):
    db, root, old, new, pid, artifact, payload, lockpath = rig
    catalogue(rig)
    old.write_bytes(payload)
    _scan_envelope(rig, **change)
    before = db.placement_get(pid)
    before_cat = db.catalog_entry_get("catalogue:original")
    result = _preview(rig)
    assert result["status"] == "blocked"
    assert result["reason"] == "placement_metadata_unavailable"
    assert "plan_token" not in result
    assert db.placement_get(pid) == before
    assert db.catalog_entry_get("catalogue:original") == before_cat


def test_metadata_timestamp_is_historical_not_file_attestation(rig):
    db, root, old, new, pid, artifact, payload, lockpath = rig
    catalogue(rig)
    old.write_bytes(payload)
    _scan_envelope(rig, mtime_ns=1)
    # Return at original path with a new mtime, exact SHA still mandatory.
    assert _preview(rig)["status"] == "ready"
    old.write_bytes(payload + b"replaced")
    assert _preview(rig)["status"] == "blocked"


def test_scanner_envelope_does_not_relax_orphan_repair(rig):
    db, root, old, new, pid, artifact, payload, lockpath = rig
    old.write_bytes(payload)
    _scan_envelope(rig)
    assert preview_reappeared_missing_placement(
        db, library_root=root, identity_id="alice", placement_id=pid,
    ) == {
        "status": "blocked", "reason": "placement_metadata_unavailable",
    }
    assert _preview(rig)["reason"] == "no_linked_catalogue"
