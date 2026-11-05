"""P0 #2847: linked catalogue and returned original are re-attested together.

Real SQLite and synthetic local bytes only; no provider, rights, or unlink.
"""
from __future__ import annotations

import json

import pytest

from cifereca.core.missing_placement_linked_same_path import (
    preview_reappeared_linked_placement,
    restore_reappeared_linked_placement,
)
from cifereca.locking import InterProcessLock
from tests.test_core_p0_2847_operator_session import _session
from tests.test_core_p0_2847_relocation_transaction import rig, catalogue


def preview(rig):
    db, root, old, new, pid, artifact, payload, lockpath = rig
    return preview_reappeared_linked_placement(
        db, identity_id="alice", placement_id=pid, library_root=root,
    )


def commit(rig, token, *, confirmed=True, locked=True):
    db, root, old, new, pid, artifact, payload, lockpath = rig
    if not locked:
        return restore_reappeared_linked_placement(
            db, identity_id="alice", placement_id=pid, library_root=root,
            plan_token=token, confirmed=confirmed,
            shared_lock=InterProcessLock(lockpath),
        )
    with InterProcessLock(lockpath) as lease:
        return restore_reappeared_linked_placement(
            db, identity_id="alice", placement_id=pid, library_root=root,
            plan_token=token, confirmed=confirmed, shared_lock=lease,
        )


def test_linked_returned_original_restores_both_sqlite_rows_only(rig):
    db, root, old, new, pid, artifact, payload, lockpath = rig
    catalogue(rig)
    old.write_bytes(payload)
    before_placement = db.placement_get(pid)
    before_catalog = db.catalog_entry_get("catalogue:original")
    p = preview(rig)
    assert p["status"] == "ready" and p["catalogue_rows"] == 1
    assert p["reason"] == "linked_catalogue_reappeared"
    assert commit(rig, p["plan_token"], confirmed=False)["reason"] == "confirmation_required"
    assert commit(rig, p["plan_token"], locked=False)["reason"] == "acquisition_lock_required"
    assert db.placement_get(pid) == before_placement
    assert db.catalog_entry_get("catalogue:original") == before_catalog
    assert commit(rig, p["plan_token"]) == {
        "status": "restored", "reason": "linked_catalogue_reappeared",
        "catalogue_rows": 1,
    }
    placed = db.placement_get(pid)
    updated = db.catalog_entry_get("catalogue:original")
    assert placed["state"] == "present" and placed["path"] == str(old)
    assert updated["local_state"] == "local"
    assert updated["metadata"] == before_catalog["metadata"]
    for key in ("placement_id", "artifact_id", "identity_id",
                "provider_id", "source_item_id"):
        assert placed[key] == before_placement[key]
    for key in ("catalog_entry_id", "placement_id", "artifact_id",
                "identity_id", "provider_id", "item_id", "title", "author"):
        assert updated[key] == before_catalog[key]
    assert db.acquisitions() == [] and db.entitlements() == []
    assert old.read_bytes() == new.read_bytes() == payload
    assert commit(rig, p["plan_token"])["reason"] == "not_missing"


def test_private_operator_one_shot_and_no_token_exposure(rig):
    db, root, old, new, pid, artifact, payload, lockpath = rig
    catalogue(rig)
    old.write_bytes(payload)
    service, operator = _session(rig)
    p = operator.preview_linked_same_path_restoration(placement_id=pid)
    assert p == {
        "status": "ready", "reason": "linked_catalogue_reappeared",
        "read_only": True, "requires_explicit_confirmation": True,
        "catalogue_rows": 1,
    }
    assert "plan_token" not in json.dumps(p)
    assert operator.confirm(user_confirmed=False)["reason"] == "confirmation_required"
    assert operator.preview_linked_same_path_restoration(placement_id=pid)["status"] == "ready"
    result = operator.confirm(user_confirmed=True)
    assert result == {
        "status": "restored", "reason": "linked_catalogue_reappeared",
        "read_only": False, "catalogue_rows": 1,
    }
    assert operator.confirm(user_confirmed=True)["reason"] == "confirmation_required"


@pytest.mark.parametrize("obstacle", [
    "no_catalogue", "foreign_owner", "wrong_edition", "backup",
    "changed_bytes", "catalogue_path_conflict", "catalogue_relative",
    "opaque_placement", "duplicate_catalogue_json",
])
def test_refuses_unsafe_linked_return_without_sql_mutation(rig, obstacle):
    db, root, old, new, pid, artifact, payload, lockpath = rig
    old.write_bytes(payload)
    if obstacle == "no_catalogue":
        pass
    elif obstacle == "foreign_owner":
        catalogue(rig, owner="bob")
    elif obstacle == "wrong_edition":
        catalogue(rig, item="other-edition")
    else:
        catalogue(rig)
    if obstacle == "backup":
        with db._lock, db._connect() as sql:
            sql.execute(
                "INSERT INTO backup_items(provider_id,credential_id,item_id,"
                "title,status,updated_at,local_path) VALUES(?,?,?,?,?,?,?)",
                ("kobo", "card", "book-a", "Legacy", "complete", 1, str(old)),
            )
    elif obstacle == "changed_bytes":
        old.write_bytes(payload + b"changed")
    elif obstacle == "catalogue_path_conflict":
        with db._lock, db._connect() as sql:
            sql.execute(
                "UPDATE catalog_entries SET metadata_json=? "
                "WHERE catalog_entry_id='catalogue:original'",
                (json.dumps({"path": str(new)}),),
            )
    elif obstacle == "catalogue_relative":
        with db._lock, db._connect() as sql:
            sql.execute(
                "UPDATE catalog_entries SET metadata_json=? "
                "WHERE catalog_entry_id='catalogue:original'",
                (json.dumps({"path": str(old), "relative_path": "old.epub"}),),
            )
    elif obstacle == "opaque_placement":
        with db._lock, db._connect() as sql:
            sql.execute(
                "UPDATE placements SET metadata_json=? WHERE placement_id=?",
                ('{"owner":"unverified"}', pid),
            )
    elif obstacle == "duplicate_catalogue_json":
        with db._lock, db._connect() as sql:
            sql.execute(
                "UPDATE catalog_entries SET metadata_json=? "
                "WHERE catalog_entry_id='catalogue:original'",
                ('{"path":"' + str(old) + '","path":""}',),
            )
    before = db.placement_get(pid)
    before_cat = db.catalog_entries()
    p = preview(rig)
    assert p["status"] == "blocked"
    assert "plan_token" not in p
    assert db.placement_get(pid) == before
    assert db.catalog_entries() == before_cat


def test_prepared_plan_invalidated_by_catalogue_change(rig):
    db, root, old, new, pid, artifact, payload, lockpath = rig
    catalogue(rig)
    old.write_bytes(payload)
    token = preview(rig)["plan_token"]
    with db._lock, db._connect() as sql:
        sql.execute(
            "UPDATE catalog_entries SET updated_at=updated_at+1 "
            "WHERE catalog_entry_id='catalogue:original'"
        )
    assert commit(rig, token)["reason"] == "stale_plan"
    assert db.placement_get(pid)["state"] == "missing"
    assert db.catalog_entry_get("catalogue:original")["local_state"] == "missing"


def test_update_failure_rolls_back_both_tables(rig):
    db, root, old, new, pid, artifact, payload, lockpath = rig
    catalogue(rig)
    old.write_bytes(payload)
    token = preview(rig)["plan_token"]
    with db._lock, db._connect() as sql:
        sql.execute(
            "CREATE TRIGGER prevent_catalogue_restore BEFORE UPDATE "
            "ON catalog_entries BEGIN SELECT RAISE(ABORT, 'synthetic'); END"
        )
    assert commit(rig, token)["reason"] == "transaction_failed"
    assert db.placement_get(pid)["state"] == "missing"
    assert db.catalog_entry_get("catalogue:original")["local_state"] == "missing"
