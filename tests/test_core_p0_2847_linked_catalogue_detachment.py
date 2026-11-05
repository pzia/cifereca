"""P0 #2847: real SQLite/FS tests of scoped catalogue detachment on retirement.

No physical unlink, no entitlement/provenance mutation, no catalog deletion.
A preview is provisional and independent references veto the final SQL state.
"""
from __future__ import annotations

import json

import pytest

from cifereca.core.missing_placement_catalogue_detachment import (
    prepare_missing_catalogue_detachment, retire_missing_linked_catalogue,
)
from cifereca.locking import InterProcessLock
from tests.test_core_p0_2847_relocation_transaction import catalogue, rig


def plan(rig):
    storage, root, old, new, pid, artifact, payload, lockpath = rig
    return prepare_missing_catalogue_detachment(
        storage, placement_id=pid, identity_id="alice", library_root=root,
    )


def commit(rig, token, *, confirmed=True, held=True):
    storage, root, old, new, pid, artifact, payload, lockpath = rig
    if not held:
        return retire_missing_linked_catalogue(
            storage, placement_id=pid, identity_id="alice",
            library_root=root, plan_token=token, confirmed=confirmed,
            shared_lock=InterProcessLock(lockpath),
        )
    with InterProcessLock(lockpath) as locked:
        return retire_missing_linked_catalogue(
            storage, placement_id=pid, identity_id="alice",
            library_root=root, plan_token=token, confirmed=confirmed,
            shared_lock=locked,
        )


def test_linked_missing_catalogue_detached_without_erasing_right_or_book(rig):
    storage, root, old, new, pid, artifact, payload, lockpath = rig
    catalogue(rig, metadata={"path": str(old), "source": "kobo"})
    before = storage.catalog_entry_get("catalogue:original")
    prepared = plan(rig)
    assert prepared["status"] == "review_required"
    assert prepared["catalogue_rows"] == 1
    assert prepared["requires_confirmation"] is True
    assert len(prepared["plan_token"]) == 64
    assert commit(rig, prepared["plan_token"], confirmed=False)["reason"] == "confirmation_required"
    assert commit(rig, prepared["plan_token"], held=False)["reason"] == "acquisition_lock_required"
    assert storage.placement_get(pid)["state"] == "missing"

    result = commit(rig, prepared["plan_token"])
    assert result == {
        "status": "retired", "reason": "catalogue_detached",
        "catalogue_rows": 1,
    }
    assert storage.placement_get(pid) is None
    after = storage.catalog_entry_get("catalogue:original")
    assert after["placement_id"] is None
    assert after["local_state"] == "missing"
    assert after["metadata"] == {"source": "kobo"}
    for key in ("catalog_entry_id", "artifact_id", "identity_id",
                "provider_id", "item_id", "title", "author"):
        assert after[key] == before[key]
    assert new.read_bytes() == payload
    assert not old.exists()
    assert storage.artifact_get(artifact) is not None
    assert commit(rig, prepared["plan_token"])["reason"] == "placement_absent"


def test_two_exact_catalogue_projections_are_detached_atomically(rig):
    storage, root, old, new, pid, artifact, payload, lockpath = rig
    catalogue(rig)
    storage.catalog_entry_put({
        "catalog_entry_id": "catalogue:additional",
        "placement_id": pid, "identity_id": "alice", "artifact_id": artifact,
        "provider_id": "kobo", "item_id": "book-a", "title": "Alternate view",
        "local_state": "missing",
        "metadata": {"path": str(old), "remote_source": "kobo"},
    })
    p = plan(rig)
    assert p["status"] == "review_required" and p["catalogue_rows"] == 2
    assert commit(rig, p["plan_token"])["catalogue_rows"] == 2
    for cid in ("catalogue:original", "catalogue:additional"):
        record = storage.catalog_entry_get(cid)
        assert record["placement_id"] is None
        assert record["local_state"] == "missing"
        assert "path" not in record["metadata"]
    assert storage.placement_get(pid) is None
    assert new.read_bytes() == payload


def test_checkpoint_mentioning_placement_id_blocks_and_rolls_back(rig):
    storage, root, old, new, pid, artifact, payload, lockpath = rig
    catalogue(rig)
    p = plan(rig)
    assert p["status"] == "review_required"
    storage.metadata_set("synthetic.pending", json.dumps({"placement_id": pid}))
    assert commit(rig, p["plan_token"])["reason"] == "unmigrated_reference"
    assert storage.placement_get(pid)["path"] == str(old)
    assert storage.catalog_entry_get("catalogue:original")["placement_id"] == pid
    assert storage.catalog_entry_get("catalogue:original")["metadata"]["path"] == str(old)


def test_backup_checkpoint_is_a_separate_migration_and_vetoes_commit(rig):
    storage, root, old, new, pid, artifact, payload, lockpath = rig
    catalogue(rig)
    p = plan(rig)
    with storage._lock, storage._connect() as db:
        db.execute(
            "INSERT INTO backup_items(provider_id,credential_id,item_id,"
            "title,status,updated_at,local_path) VALUES(?,?,?,?,?,?,?)",
            ("kobo", "private-card", "book-a", "Synthetic",
             "complete", 1, str(old)),
        )
    assert commit(rig, p["plan_token"])["reason"] == "backup_requires_separate_relink"
    assert storage.placement_get(pid)["path"] == str(old)
    assert storage.catalog_entry_get("catalogue:original")["placement_id"] == pid


def test_reappearing_missing_file_never_removed_or_unlinked(rig):
    storage, root, old, new, pid, artifact, payload, lockpath = rig
    catalogue(rig)
    p = plan(rig)
    old.write_bytes(payload)
    assert commit(rig, p["plan_token"])["reason"] == "old_path_not_verified_absent"
    assert old.read_bytes() == payload
    assert storage.placement_get(pid)["state"] == "missing"


@pytest.mark.parametrize("conflict", ["foreign_identity", "wrong_edition",
                                      "nested_path", "relative_path",
                                      "duplicate_json"])
def test_unattested_catalogue_scope_or_metadata_refuses(rig, conflict):
    storage, root, old, new, pid, artifact, payload, lockpath = rig
    if conflict == "foreign_identity":
        catalogue(rig, owner="bob")
    elif conflict == "wrong_edition":
        catalogue(rig, item="other-book")
    elif conflict == "nested_path":
        catalogue(rig, metadata={
            "path": str(old), "nested": {"historical_link": str(old)},
        })
    elif conflict == "relative_path":
        catalogue(rig, metadata={
            "path": str(old), "relative_path": "kobo/old.epub",
        })
    else:
        catalogue(rig)
        with storage._lock, storage._connect() as db:
            db.execute(
                "UPDATE catalog_entries SET metadata_json=? "
                "WHERE catalog_entry_id='catalogue:original'",
                ('{"path":"' + str(old) + '","path":""}',),
            )
    p = plan(rig)
    assert p["status"] == "blocked"
    assert storage.placement_get(pid)["state"] == "missing"
    assert storage.catalog_entry_get("catalogue:original")["placement_id"] == pid


def test_sql_trigger_failure_rolls_back_both_tables(rig):
    storage, root, old, new, pid, artifact, payload, lockpath = rig
    catalogue(rig)
    p = plan(rig)
    with storage._lock, storage._connect() as db:
        db.execute(
            "CREATE TRIGGER force_catalogue_rollback BEFORE UPDATE "
            "ON catalog_entries BEGIN SELECT RAISE(ABORT, 'fixture abort'); END"
        )
    assert commit(rig, p["plan_token"])["reason"] == "transaction_failed"
    assert storage.placement_get(pid)["state"] == "missing"
    assert storage.catalog_entry_get("catalogue:original")["placement_id"] == pid
    assert storage.catalog_entry_get("catalogue:original")["metadata"]["path"] == str(old)


def test_stale_catalogue_projection_invalidates_prepared_plan(rig):
    storage, root, old, new, pid, artifact, payload, lockpath = rig
    catalogue(rig)
    p = plan(rig)
    with storage._lock, storage._connect() as db:
        db.execute(
            "UPDATE catalog_entries SET metadata_json=? "
            "WHERE catalog_entry_id='catalogue:original'",
            ('{"path":"' + str(old) + '","edition_update":true}',),
        )
    assert commit(rig, p["plan_token"])["reason"] == "stale_plan"
    assert storage.placement_get(pid)["path"] == str(old)
    assert storage.catalog_entry_get("catalogue:original")["placement_id"] == pid


def test_missing_artifact_cannot_lose_its_only_historical_projection(rig):
    storage, root, old, new, pid, artifact, payload, lockpath = rig
    catalogue(rig)
    with storage._lock, storage._connect() as db:
        db.execute("DELETE FROM artifacts WHERE artifact_id=?", (artifact,))
    assert plan(rig)["reason"] == "artifact_evidence_unavailable"
    assert storage.placement_get(pid)["state"] == "missing"
    assert storage.catalog_entry_get("catalogue:original")["placement_id"] == pid


def test_only_orphan_path_does_not_use_linked_catalogue_route(rig):
    storage, root, old, new, pid, artifact, payload, lockpath = rig
    assert plan(rig)["reason"] == "no_linked_catalogue"
    assert storage.placement_get(pid)["state"] == "missing"



def test_linked_detachment_refuses_replaced_profile_directory_after_preview(
    rig, tmp_path,
):
    import shutil

    storage, root, old, new, pid, artifact, payload, _lockpath = rig
    catalogue(rig)
    token = plan(rig)["plan_token"]
    before_placement = storage.placement_get(pid)
    before_catalogue = storage.catalog_entry_get("catalogue:original")
    owner = root / "alice"
    moved = tmp_path / "original-alice"
    root_ino = root.stat().st_ino
    owner.rename(moved)
    (owner / "kobo").mkdir(parents=True)
    try:
        result = commit(rig, token)
        assert result == {"status": "blocked", "reason": "stale_plan"}
        assert root.stat().st_ino == root_ino
        assert storage.placement_get(pid) == before_placement
        assert storage.catalog_entry_get("catalogue:original") == before_catalogue
    finally:
        shutil.rmtree(owner)
        moved.rename(owner)
    assert commit(rig, token)["status"] == "retired"
    assert new.read_bytes() == payload


def test_linked_detachment_detects_profile_swap_inside_sql_commit(
    rig, tmp_path, monkeypatch,
):
    import shutil
    from cifereca.core import missing_placement_catalogue_detachment as module

    storage, root, old, new, pid, artifact, payload, _lockpath = rig
    catalogue(rig)
    token = plan(rig)["plan_token"]
    before_placement = storage.placement_get(pid)
    before_catalogue = storage.catalog_entry_get("catalogue:original")
    owner = root / "alice"
    moved = tmp_path / "alice-at-commit"
    real = module._directory_signature
    calls = []

    def replace_on_final_probe(path):
        calls.append(path)
        if len(calls) == 2:
            owner.rename(moved)
            (owner / "kobo").mkdir(parents=True)
        return real(path)

    try:
        with monkeypatch.context() as patch:
            patch.setattr(module, "_directory_signature", replace_on_final_probe)
            result = commit(rig, token)
        assert result == {"status": "blocked", "reason": "filesystem_changed"}
        assert storage.placement_get(pid) == before_placement
        assert storage.catalog_entry_get("catalogue:original") == before_catalogue
    finally:
        if moved.exists():
            shutil.rmtree(owner)
            moved.rename(owner)
    assert commit(rig, token)["status"] == "retired"
    assert new.read_bytes() == payload
