"""#2847: real SQLite/FS tests of the explicit relocation *commit* boundary."""
from __future__ import annotations

import hashlib
import json

import pytest

import cifereca.core.canonical_recovery as recovery
from cifereca.core.missing_placement_relocation_preview import (
    preview_missing_placement_relocation,
)
from cifereca.core.missing_placement_relocation_transaction import (
    relocate_missing_placement,
)
from cifereca.locking import InterProcessLock
from cifereca.storage import Storage


@pytest.fixture
def rig(tmp_path, monkeypatch):
    root = tmp_path / "library"
    folder = root / "alice" / "kobo"
    folder.mkdir(parents=True)
    (root / "bob").mkdir()
    db = Storage(tmp_path / "db" / "library.sqlite3")
    db.identity_put({"identity_id": "alice", "directory_name": "alice", "label": "Alice"})
    db.identity_put({"identity_id": "bob", "directory_name": "bob", "label": "Bob"})
    old = folder / "old.epub"
    new = folder / "new.epub"
    payload = b"synthetic content to verify before any SQLite commit"
    new.write_bytes(payload)
    sha = hashlib.sha256(payload).hexdigest()
    artifact_id = "sha256:" + sha
    db.artifact_put({
        "artifact_id": artifact_id, "sha256": sha,
        "size": len(payload), "media_format": "epub",
    })
    pid = db.placement_put({
        "path": str(old), "identity_id": "alice",
        "provider_id": "kobo", "source_item_id": "book-a",
        "artifact_id": artifact_id, "state": "missing", "metadata": {},
    })
    lockpath = tmp_path / "acquisition.lock"
    monkeypatch.setattr(recovery, "acquisition_lock_file", lambda: lockpath)
    yield db, root, old, new, pid, artifact_id, payload, lockpath
    db.close()


def prepare(rig):
    db, root, old, new, pid, artifact, payload, lockpath = rig
    return preview_missing_placement_relocation(
        db, library_root=root, identity_id="alice",
        placement_id=pid, candidate_path=new,
    )


def commit(rig, token: str, *, confirmed=True, use_lock=True):
    db, root, old, new, pid, artifact, payload, lockpath = rig
    if not use_lock:
        return relocate_missing_placement(
            db, library_root=root, identity_id="alice", placement_id=pid,
            candidate_path=new, plan_token=token, confirmed=confirmed,
            shared_lock=InterProcessLock(lockpath),
        )
    with InterProcessLock(lockpath) as locked:
        return relocate_missing_placement(
            db, library_root=root, identity_id="alice", placement_id=pid,
            candidate_path=new, plan_token=token, confirmed=confirmed,
            shared_lock=locked,
        )


def catalogue(rig, *, owner="alice", item="book-a", metadata=None):
    db, root, old, new, pid, artifact, payload, lockpath = rig
    db.catalog_entry_put({
        "catalog_entry_id": "catalogue:original",
        "placement_id": pid, "identity_id": owner, "artifact_id": artifact,
        "provider_id": "kobo", "item_id": item, "title": "Synthetic",
        "local_state": "missing",
        "metadata": metadata if metadata is not None else {"path": str(old)},
    })


def test_operator_confirmed_relink_restores_same_owned_catalogue_only(rig):
    db, root, old, new, pid, artifact, payload, lockpath = rig
    catalogue(rig)
    before = db.catalog_entry_get("catalogue:original")
    prepared = prepare(rig)
    assert prepared["status"] == "review_required"
    assert prepared["reason"] == "references_require_migration"
    assert commit(rig, prepared["plan_token"], confirmed=False)["reason"] == "confirmation_required"
    assert db.placement_get(pid)["path"] == str(old)
    outcome = commit(rig, prepared["plan_token"])
    assert outcome == {"status": "relocated", "catalogue_rows": 1}
    placed = db.placement_get(pid)
    assert placed["path"] == str(new)
    assert placed["state"] == "present"
    updated = db.catalog_entry_get("catalogue:original")
    assert updated["local_state"] == "local"
    assert updated["metadata"]["path"] == str(new)
    assert updated["item_id"] == before["item_id"]
    assert updated["artifact_id"] == before["artifact_id"]
    assert new.read_bytes() == payload
    assert not old.exists()
    assert commit(rig, prepared["plan_token"])["reason"] == "not_missing"


def test_orphan_can_be_relinked_without_creating_a_catalogue(rig):
    db, root, old, new, pid, artifact, payload, lockpath = rig
    prepared = prepare(rig)
    assert prepared["reason"] == "exact_bytes_candidate"
    assert commit(rig, prepared["plan_token"]) == {
        "status": "relocated", "catalogue_rows": 0,
    }
    assert db.placement_get(pid)["state"] == "present"
    assert db.placement_get(pid)["path"] == str(new)
    assert db.catalog_entries() == []


def test_unheld_acquisition_lock_cannot_commit(rig):
    db, root, old, new, pid, artifact, payload, lockpath = rig
    token = prepare(rig)["plan_token"]
    assert commit(rig, token, use_lock=False)["reason"] == "invalid_request"
    assert db.placement_get(pid)["state"] == "missing"


def test_modified_target_after_preview_cannot_relink(rig):
    db, root, old, new, pid, artifact, payload, lockpath = rig
    token = prepare(rig)["plan_token"]
    new.write_bytes(payload + b"bad")
    assert commit(rig, token)["reason"] == "candidate_bytes_unverified"
    assert db.placement_get(pid)["state"] == "missing"


def test_old_path_return_blocks_action(rig):
    db, root, old, new, pid, artifact, payload, lockpath = rig
    token = prepare(rig)["plan_token"]
    old.write_bytes(payload)
    assert commit(rig, token)["reason"] == "old_path_not_verified_absent"
    assert db.placement_get(pid)["path"] == str(old)


def test_independent_checkpoint_rolls_back_all_sql_updates(rig):
    db, root, old, new, pid, artifact, payload, lockpath = rig
    catalogue(rig)
    token = prepare(rig)["plan_token"]
    db.metadata_set("synthetic.pending", json.dumps({"path": str(old)}))
    result = commit(rig, token)
    assert result["status"] == "blocked"
    assert db.placement_get(pid)["state"] == "missing"
    assert db.catalog_entry_get("catalogue:original")["metadata"]["path"] == str(old)
    assert new.read_bytes() == payload


def test_id_only_checkpoint_blocks_relink_without_path(rig):
    db, root, old, new, pid, artifact, payload, lockpath = rig
    token = prepare(rig)["plan_token"]
    db.metadata_set("synthetic.pending", json.dumps({"placement_id": pid}))
    assert commit(rig, token)["status"] == "blocked"
    assert db.placement_get(pid)["path"] == str(old)


@pytest.mark.parametrize("conflict", ["wrong_identity", "wrong_item", "relative_path"])
def test_unsafe_catalogue_coordinates_or_metadata_block(rig, conflict):
    db, root, old, new, pid, artifact, payload, lockpath = rig
    if conflict == "wrong_identity":
        catalogue(rig, owner="bob")
    elif conflict == "wrong_item":
        catalogue(rig, item="another-edition")
    else:
        catalogue(rig, metadata={
            "path": str(old), "relative_path": "kobo/old.epub",
        })
    token = prepare(rig)["plan_token"]
    outcome = commit(rig, token)
    assert outcome["status"] == "blocked"
    assert db.placement_get(pid)["state"] == "missing"
    assert db.catalog_entry_get("catalogue:original")["local_state"] == "missing"


def test_unattested_old_backup_reference_blocks_relocation(rig):
    db, root, old, new, pid, artifact, payload, lockpath = rig
    token = prepare(rig)["plan_token"]
    with db._lock, db._connect() as sql:
        sql.execute(
            "INSERT INTO backup_items(provider_id,credential_id,item_id,"
            "title,status,updated_at,local_path) VALUES(?,?,?,?,?,?,?)",
            ("kobo", "card", "book-a", "Synthetic", "complete", 1, str(old)),
        )
    assert commit(rig, token)["status"] == "blocked"
    assert db.placement_get(pid)["state"] == "missing"


@pytest.mark.parametrize("field", ["catalogue", "placement"])
def test_duplicate_json_metadata_keys_are_never_rewritten(rig, field):
    db, root, old, new, pid, artifact, payload, lockpath = rig
    catalogue(rig)
    with db._lock, db._connect() as sql:
        if field == "catalogue":
            # Duplicate path keys are ambiguous even if the last wins
            # after json.loads. Never silently discard the earlier value.
            sql.execute(
                "UPDATE catalog_entries SET metadata_json=? "
                "WHERE catalog_entry_id='catalogue:original'",
                ('{"path":"'+str(new)+'","path":"'+str(old)+'"}',),
            )
        else:
            sql.execute(
                "UPDATE placements SET metadata_json=? WHERE placement_id=?",
                ('{"source":"old","source":"new"}', pid),
            )
    token = prepare(rig)["plan_token"]
    assert commit(rig, token)["reason"] == "transaction_failed"
    assert db.placement_get(pid)["path"] == str(old)
    assert db.placement_get(pid)["state"] == "missing"
    assert db.catalog_entry_get("catalogue:original")["local_state"] == "missing"


def test_sql_abort_rolls_back_catalogue_and_placement(rig):
    db, root, old, new, pid, artifact, payload, lockpath = rig
    catalogue(rig)
    token = prepare(rig)["plan_token"]
    with db._lock, db._connect() as sql:
        sql.execute(
            "CREATE TRIGGER deny_relink BEFORE UPDATE ON catalog_entries "
            "BEGIN SELECT RAISE(ABORT, 'test interruption'); END"
        )
    assert commit(rig, token)["reason"] == "transaction_failed"
    assert db.placement_get(pid)["path"] == str(old)
    assert db.catalog_entry_get("catalogue:original")["metadata"]["path"] == str(old)
    with db._lock, db._connect() as sql:
        sql.execute("DROP TRIGGER deny_relink")
    assert commit(rig, token)["status"] == "relocated"


def test_target_occupied_after_preview_never_steals_other_placement(rig):
    db, root, old, new, pid, artifact, payload, lockpath = rig
    token = prepare(rig)["plan_token"]
    existing = db.placement_put({
        "path": str(new), "state": "present",
        "identity_id": "alice", "provider_id": "local",
        "source_item_id": "different-book", "artifact_id": artifact,
    })
    assert commit(rig, token)["reason"] == "target_placement_occupied"
    assert db.placement_get(existing)["state"] == "present"
    assert db.placement_get(pid)["state"] == "missing"



@pytest.mark.parametrize("swap_on_probe", [2, 3])
def test_relocation_rejects_profile_directory_swap_before_sql_commit(
    rig, tmp_path, monkeypatch, swap_on_probe,
):
    """Even same-SHA replacement bytes cannot move identity ownership."""
    import shutil
    from cifereca.core import missing_placement_relocation_transaction as module

    db, root, old, new, pid, artifact, payload, _lockpath = rig
    catalogue(rig)
    token = prepare(rig)["plan_token"]
    before_placement = db.placement_get(pid)
    before_catalogue = db.catalog_entry_get("catalogue:original")
    owner = root / "alice"
    moved = tmp_path / "alice-original-profile"
    root_ino = root.stat().st_ino
    actual = module._directory_signature
    calls = []

    def rotate_profile_during_final_verification(path):
        calls.append(path)
        if len(calls) == swap_on_probe:
            owner.rename(moved)
            (owner / "kobo").mkdir(parents=True)
        return actual(path)

    try:
        with monkeypatch.context() as patch:
            patch.setattr(module, "_directory_signature",
                          rotate_profile_during_final_verification)
            result = commit(rig, token)
        assert result["status"] == "blocked"
        assert result["reason"] == (
            "candidate_changed" if swap_on_probe == 2 else "filesystem_changed"
        )
        assert root.stat().st_ino == root_ino
        assert db.placement_get(pid) == before_placement
        assert db.catalog_entry_get("catalogue:original") == before_catalogue
    finally:
        if moved.exists():
            shutil.rmtree(owner)
            moved.rename(owner)

    assert commit(rig, token) == {"status": "relocated", "catalogue_rows": 1}
    assert new.read_bytes() == payload
