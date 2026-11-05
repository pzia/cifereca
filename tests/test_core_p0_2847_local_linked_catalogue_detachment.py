"""P0 #2847: a truly local-only, missing Placement can detach its catalogue.

All paths and SQLite rows are synthetic. No physical deletion, supplier I/O
or conversion of a missing local file into a provider ownership claim.
"""
from __future__ import annotations

import pytest

from tests.test_core_p0_2847_relocation_transaction import rig
from tests.test_core_p0_2847_linked_catalogue_detachment import plan, commit


def _local_only(rig, *, provider=None, item=None, catalogue_provider=None,
                catalogue_item=None):
    storage, root, old, new, pid, artifact, payload, lockpath = rig
    with storage._lock, storage._connect() as db:
        db.execute(
            "UPDATE placements SET provider_id=?, source_item_id=? "
            "WHERE placement_id=?",
            (provider, item, pid),
        )
    storage.catalog_entry_put({
        "catalog_entry_id": "catalogue:local-only",
        "placement_id": pid, "identity_id": "alice",
        "artifact_id": artifact,
        "provider_id": catalogue_provider, "item_id": catalogue_item,
        "title": "Imported local item", "local_state": "missing",
        "metadata": {"path": str(old)},
    })


def test_local_only_linked_placement_detaches_without_erasing_catalogue(rig):
    storage, root, old, new, pid, artifact, payload, lockpath = rig
    _local_only(rig)
    before = storage.catalog_entry_get("catalogue:local-only")
    reviewed = plan(rig)
    assert reviewed["status"] == "review_required"
    assert reviewed["reason"] == "catalogue_detachment_requires_confirmation"
    assert reviewed["catalogue_rows"] == 1
    assert reviewed["requires_confirmation"] is True
    assert commit(rig, reviewed["plan_token"], confirmed=False)["reason"] == "confirmation_required"
    assert commit(rig, reviewed["plan_token"], held=False)["reason"] == "acquisition_lock_required"
    assert storage.placement_get(pid)["state"] == "missing"

    result = commit(rig, reviewed["plan_token"])
    assert result == {
        "status": "retired", "reason": "catalogue_detached",
        "catalogue_rows": 1,
    }
    assert storage.placement_get(pid) is None
    detached = storage.catalog_entry_get("catalogue:local-only")
    assert detached["placement_id"] is None
    assert detached["local_state"] == "missing"
    assert detached["metadata"] == {}
    for field in ("catalog_entry_id", "artifact_id", "identity_id",
                  "provider_id", "item_id", "title", "author"):
        assert detached[field] == before[field]
    assert detached["provider_id"] is None and detached["item_id"] is None
    assert storage.artifact_get(artifact) is not None
    assert not old.exists()
    assert new.read_bytes() == payload
    assert commit(rig, reviewed["plan_token"])["reason"] == "placement_absent"


@pytest.mark.parametrize("provider,item", [
    ("kobo", None), (None, "book-a"), ("", ""), ("kobo", ""),
])
def test_partially_known_source_can_never_be_treated_as_local_only(
    rig, provider, item,
):
    storage, root, old, new, pid, artifact, payload, lockpath = rig
    _local_only(
        rig, provider=provider, item=item,
        catalogue_provider=provider, catalogue_item=item,
    )
    result = plan(rig)
    assert result["status"] == "blocked"
    assert result["reason"] == "source_coordinates_unavailable"
    assert storage.placement_get(pid)["state"] == "missing"
    assert storage.catalog_entry_get("catalogue:local-only")["placement_id"] == pid


@pytest.mark.parametrize("catalogue_provider,catalogue_item", [
    ("kobo", "book-a"), ("kobo", None), (None, "book-a"),
])
def test_supplier_catalogue_cannot_detach_through_local_only_scope(
    rig, catalogue_provider, catalogue_item,
):
    storage, root, old, new, pid, artifact, payload, lockpath = rig
    _local_only(
        rig, catalogue_provider=catalogue_provider,
        catalogue_item=catalogue_item,
    )
    result = plan(rig)
    assert result["status"] == "blocked"
    assert result["reason"] == "catalogue_scope_conflict"
    assert storage.placement_get(pid)["state"] == "missing"
    assert storage.catalog_entry_get("catalogue:local-only")["placement_id"] == pid


def test_local_only_requires_fresh_physical_absence_at_commit(rig):
    storage, root, old, new, pid, artifact, payload, lockpath = rig
    _local_only(rig)
    reviewed = plan(rig)
    assert reviewed["status"] == "review_required"
    old.write_bytes(payload)
    result = commit(rig, reviewed["plan_token"])
    assert result["status"] == "blocked"
    assert result["reason"] == "old_path_not_verified_absent"
    assert storage.placement_get(pid)["state"] == "missing"
    assert storage.catalog_entry_get("catalogue:local-only")["placement_id"] == pid
    assert old.read_bytes() == payload


def test_local_only_path_claimed_by_backup_remains_unmodified(rig):
    storage, root, old, new, pid, artifact, payload, lockpath = rig
    _local_only(rig)
    reviewed = plan(rig)
    with storage._lock, storage._connect() as db:
        db.execute(
            "INSERT INTO backup_items("
            "provider_id,credential_id,item_id,title,status,updated_at,local_path)"
            " VALUES(?,?,?,?,?,?,?)",
            ("kobo", "other-credential", "edition-b", "Synthetic",
             "complete", 1, str(old)),
        )
    result = commit(rig, reviewed["plan_token"])
    assert result["status"] == "blocked"
    assert result["reason"] == "backup_requires_separate_relink"
    assert storage.placement_get(pid)["state"] == "missing"
    assert storage.catalog_entry_get("catalogue:local-only")["placement_id"] == pid
