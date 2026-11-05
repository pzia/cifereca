"""P0 #2847: relocate a manually moved local import without inventing source.

The target is a same-profile, SHA/inode-attested existing file. The operator
moves SQLite path references only; no file or right/provenance is mutated.
"""
from __future__ import annotations

import json

import pytest

from tests.test_core_p0_2847_relocation_transaction import rig, prepare, commit


def _local(rig, *, provider=None, item=None, catalogue=False,
           catalogue_provider=None, catalogue_item=None):
    storage, root, old, new, pid, artifact, payload, lockpath = rig
    with storage._lock, storage._connect() as db:
        db.execute(
            "UPDATE placements SET provider_id=?,source_item_id=? WHERE placement_id=?",
            (provider, item, pid),
        )
    if catalogue:
        storage.catalog_entry_put({
            "catalog_entry_id": "catalogue:local",
            "placement_id": pid, "identity_id": "alice",
            "artifact_id": artifact, "title": "Local import",
            "provider_id": catalogue_provider,
            "item_id": catalogue_item, "local_state": "missing",
            "metadata": {"path": str(old)},
        })


@pytest.mark.parametrize("catalogue", [False, True])
def test_local_import_relinks_only_its_exact_physical_placement(
    rig, catalogue,
):
    storage, root, old, new, pid, artifact, payload, lockpath = rig
    _local(rig, catalogue=catalogue)
    before_bytes = new.read_bytes()
    p = prepare(rig)
    assert p["status"] == "review_required"
    assert p["candidate_bytes_verified"] is True
    assert p["catalogue_rows"] == int(catalogue)
    assert commit(rig, p["plan_token"], confirmed=False)["reason"] == "confirmation_required"
    assert commit(rig, p["plan_token"], use_lock=False)["reason"] == "invalid_request"
    assert storage.placement_get(pid)["state"] == "missing"

    outcome = commit(rig, p["plan_token"])
    assert outcome == {"status": "relocated", "catalogue_rows": int(catalogue)}
    placement = storage.placement_get(pid)
    assert placement["state"] == "present"
    assert placement["path"] == str(new)
    assert placement["provider_id"] is None
    assert placement["source_item_id"] is None
    assert placement["artifact_id"] == artifact
    assert storage.artifact_get(artifact) is not None
    if catalogue:
        record = storage.catalog_entry_get("catalogue:local")
        assert record["placement_id"] == pid
        assert record["local_state"] == "local"
        assert record["metadata"] == {"path": str(new)}
        assert record["provider_id"] is None and record["item_id"] is None
    assert new.read_bytes() == before_bytes == payload
    assert not old.exists()
    assert commit(rig, p["plan_token"])["reason"] == "not_missing"


@pytest.mark.parametrize("provider,item", [
    ("kobo", None), (None, "book-a"), ("", ""), ("kobo", ""),
])
def test_partial_or_empty_coordinates_are_not_local_imports(
    rig, provider, item,
):
    storage, root, old, new, pid, artifact, payload, lockpath = rig
    _local(rig, provider=provider, item=item)
    check = prepare(rig)
    assert check == {
        "status": "blocked", "reason": "source_coordinates_unavailable",
    }
    assert storage.placement_get(pid)["state"] == "missing"
    assert new.read_bytes() == payload


@pytest.mark.parametrize("catalogue_provider,catalogue_item", [
    ("kobo", "book-a"), ("kobo", None), (None, "book-a"),
])
def test_foreign_or_partial_catalogue_can_never_gain_local_relink(
    rig, catalogue_provider, catalogue_item,
):
    storage, root, old, new, pid, artifact, payload, lockpath = rig
    _local(
        rig, catalogue=True, catalogue_provider=catalogue_provider,
        catalogue_item=catalogue_item,
    )
    p = prepare(rig)
    assert p["status"] == "review_required"
    result = commit(rig, p["plan_token"])
    assert result["status"] == "blocked"
    assert result["reason"] == "catalogue_scope_conflict"
    assert storage.placement_get(pid)["state"] == "missing"
    assert storage.catalog_entry_get("catalogue:local")["placement_id"] == pid
    assert new.read_bytes() == payload


def test_local_relink_never_adopts_backup_claim_on_old_path(rig):
    storage, root, old, new, pid, artifact, payload, lockpath = rig
    _local(rig, catalogue=True)
    p = prepare(rig)
    assert p["status"] == "review_required"
    with storage._lock, storage._connect() as db:
        db.execute(
            "INSERT INTO backup_items("
            "provider_id,credential_id,item_id,title,status,updated_at,local_path)"
            " VALUES(?,?,?,?,?,?,?)",
            ("kobo", "foreign-card", "foreign-edition",
             "Legacy unrelated", "complete", 1, str(old)),
        )
    result = commit(rig, p["plan_token"])
    assert result["status"] == "blocked"
    # The new backup invalidates the preview digest before the writer even
    # reaches its separate-link check: stale consent is never authorization.
    assert result["reason"] == "stale_plan"
    assert storage.placement_get(pid)["path"] == str(old)
    assert storage.catalog_entry_get("catalogue:local")["metadata"]["path"] == str(old)


def test_local_relink_with_existing_backup_refuses_source_rebinding(rig):
    storage, root, old, new, pid, artifact, payload, lockpath = rig
    _local(rig, catalogue=True)
    with storage._lock, storage._connect() as db:
        db.execute(
            "INSERT INTO backup_items("
            "provider_id,credential_id,item_id,title,status,updated_at,local_path)"
            " VALUES(?,?,?,?,?,?,?)",
            ("kobo", "foreign-card", "foreign-edition",
             "Legacy unrelated", "complete", 1, str(old)),
        )
    p = prepare(rig)
    assert p["status"] == "review_required"
    result = commit(rig, p["plan_token"])
    assert result["status"] == "blocked"
    assert result["reason"] == "backup_requires_separate_relink"
    assert storage.placement_get(pid)["state"] == "missing"
    assert storage.catalog_entry_get("catalogue:local")["placement_id"] == pid


def test_local_relink_refuses_id_only_checkpoint_even_with_verified_target(rig):
    storage, root, old, new, pid, artifact, payload, lockpath = rig
    _local(rig, catalogue=True)
    p = prepare(rig)
    assert p["status"] == "review_required"
    storage.metadata_set("suspended.acquisition", json.dumps({"placement_id": pid}))
    result = commit(rig, p["plan_token"])
    assert result["status"] == "blocked"
    assert storage.placement_get(pid)["state"] == "missing"
    assert storage.catalog_entry_get("catalogue:local")["metadata"]["path"] == str(old)
