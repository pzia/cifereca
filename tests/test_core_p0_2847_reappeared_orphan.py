"""P0 #2847: a missing orphan whose exact bytes return needs explicit repair.

Disposable SQLite/FS only, no provider/HTTP, no file mutation or entitlement
synthesis. Existing relocation/retirement remain separate operations.
"""
from __future__ import annotations

import json

import pytest

from cifereca.core.missing_placement_operator import MissingPlacementOperatorSession
from cifereca.core.missing_placement_same_path import (
    preview_reappeared_missing_placement,
    restore_reappeared_missing_placement,
)
from cifereca.locking import InterProcessLock
from tests.test_core_p0_2847_operator_session import _Service, _session
from tests.test_core_p0_2847_relocation_transaction import catalogue, rig


def _preview(rig):
    db, root, old, new, pid, artifact, payload, lockpath = rig
    return preview_reappeared_missing_placement(
        db, identity_id="alice", placement_id=pid, library_root=root,
    )


def _commit(rig, token, *, confirm=True, locked=True):
    db, root, old, new, pid, artifact, payload, lockpath = rig
    if not locked:
        return restore_reappeared_missing_placement(
            db, identity_id="alice", placement_id=pid, library_root=root,
            plan_token=token, confirmed=confirm,
            shared_lock=InterProcessLock(lockpath),
        )
    with InterProcessLock(lockpath) as lease:
        return restore_reappeared_missing_placement(
            db, identity_id="alice", placement_id=pid, library_root=root,
            plan_token=token, confirmed=confirm, shared_lock=lease,
        )


def test_orphan_returned_bytes_restore_only_original_sqlite_state(rig):
    db, root, old, new, pid, artifact, payload, lockpath = rig
    old.write_bytes(payload)
    before = db.placement_get(pid)
    pre = _preview(rig)
    assert pre["status"] == "ready"
    assert pre["reason"] == "orphan_reappeared"
    assert pre["requires_confirmation"] is True
    assert pre["plan_token"] and len(pre["plan_token"]) == 64
    assert db.placement_get(pid) == before
    assert _commit(rig, pre["plan_token"], confirm=False)["reason"] == "confirmation_required"
    result = _commit(rig, pre["plan_token"])
    assert result == {"status": "restored", "reason": "orphan_reappeared"}
    placed = db.placement_get(pid)
    assert placed["state"] == "present"
    assert placed["path"] == str(old)
    assert placed["artifact_id"] == artifact
    assert placed["identity_id"] == "alice"
    assert db.catalog_entries() == []
    assert db.entitlements() == []
    assert db.acquisitions() == []
    with db._lock, db._connect() as sql:
        assert sql.execute("SELECT COUNT(*) FROM origin_links").fetchone()[0] == 0
        assert sql.execute("SELECT COUNT(*) FROM source_references").fetchone()[0] == 0
    assert old.read_bytes() == new.read_bytes() == payload
    assert _commit(rig, pre["plan_token"])["reason"] == "not_missing"


def test_operator_restoration_is_private_one_shot_and_requires_full_audit(rig):
    db, root, old, new, pid, artifact, payload, lockpath = rig
    old.write_bytes(payload)
    service, operator = _session(rig)
    preview = operator.preview_same_path_restoration(placement_id=pid)
    assert preview == {
        "status": "ready", "reason": "orphan_reappeared",
        "read_only": True, "requires_explicit_confirmation": True,
    }
    assert "plan_token" not in json.dumps(preview)
    assert operator.confirm(user_confirmed=False)["reason"] == "confirmation_required"
    assert db.placement_get(pid)["state"] == "missing"
    assert operator.confirm(user_confirmed=True)["reason"] == "confirmation_required"
    assert operator.preview_same_path_restoration(placement_id=pid)["status"] == "ready"
    assert operator.confirm(user_confirmed=True) == {
        "status": "restored", "reason": "orphan_reappeared", "read_only": False,
    }
    assert operator.confirm(user_confirmed=True)["reason"] == "confirmation_required"
    assert db.placement_get(pid)["state"] == "present"

    disabled = MissingPlacementOperatorSession(
        service, identity_id="alice", scope_valid=lambda: True,
        inspection_complete=False,
    )
    assert disabled.preview_same_path_restoration(placement_id=pid) == {
        "status": "blocked", "reason": "incomplete_inspection",
        "read_only": True,
    }


@pytest.mark.parametrize("obstacle", [
    "missing", "changed", "symlink", "catalogue", "legacy_backup", "foreign",
])
def test_restoration_refuses_unverified_or_dependent_historical_path(rig, obstacle):
    db, root, old, new, pid, artifact, payload, lockpath = rig
    if obstacle != "missing":
        old.write_bytes(payload)
    if obstacle == "changed":
        old.write_bytes(payload + b" changed")
    elif obstacle == "symlink":
        old.unlink()
        old.symlink_to(new)
    elif obstacle == "catalogue":
        catalogue(rig)
    elif obstacle == "legacy_backup":
        with db._lock, db._connect() as sql:
            sql.execute(
                "INSERT INTO backup_items("
                "provider_id,credential_id,item_id,title,status,updated_at,local_path"
                ") VALUES(?,?,?,?,?,?,?)",
                ("kobo", "card", "book-a", "Legacy", "complete", 1, str(old)),
            )
    elif obstacle == "foreign":
        owner = db.placement_get(pid)
        with db._lock, db._connect() as sql:
            sql.execute(
                "UPDATE placements SET identity_id='bob' WHERE placement_id=?",
                (pid,),
            )
    saved = db.placement_get(pid)
    outcome = _preview(rig)
    assert outcome["status"] == "blocked"
    assert "plan_token" not in outcome
    assert db.placement_get(pid) == saved
    if obstacle == "catalogue":
        assert db.catalog_entry_get("catalogue:original")["local_state"] == "missing"
    else:
        assert db.catalog_entries() == []


def test_preview_token_revoked_on_file_change_and_sqlite_rebind(rig):
    db, root, old, new, pid, artifact, payload, lockpath = rig
    old.write_bytes(payload)
    token = _preview(rig)["plan_token"]
    old.write_bytes(payload + b" changed")
    assert _commit(rig, token)["status"] == "blocked"
    assert db.placement_get(pid)["state"] == "missing"
    old.write_bytes(payload)
    # Even a valid original byte sequence is not an unchanged original plan:
    # its inode/mtime may have changed during the intervening modification.
    assert _commit(rig, token)["reason"] == "stale_plan"
    fresh = _preview(rig)["plan_token"]
    with db._lock, db._connect() as sql:
        sql.execute(
            "UPDATE placements SET updated_at=updated_at+7 WHERE placement_id=?",
            (pid,),
        )
    assert _commit(rig, fresh)["reason"] == "stale_plan"
    assert db.placement_get(pid)["state"] == "missing"


def test_restoration_without_held_acquisition_lock_cannot_write(rig):
    db, root, old, new, pid, artifact, payload, lockpath = rig
    old.write_bytes(payload)
    token = _preview(rig)["plan_token"]
    assert _commit(rig, token, locked=False)["reason"] == "acquisition_lock_required"
    assert db.placement_get(pid)["state"] == "missing"


def test_transaction_abort_rolls_back_original_placement(rig):
    db, root, old, new, pid, artifact, payload, lockpath = rig
    old.write_bytes(payload)
    token = _preview(rig)["plan_token"]
    with db._lock, db._connect() as sql:
        sql.execute(
            "CREATE TRIGGER deny_restore BEFORE UPDATE ON placements "
            "BEGIN SELECT RAISE(ABORT,'synthetic rollback'); END"
        )
    assert _commit(rig, token)["reason"] == "transaction_failed"
    assert db.placement_get(pid)["state"] == "missing"
    assert old.read_bytes() == payload
