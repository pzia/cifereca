"""#2847: the only allowed retirement is an explicitly confirmed SQL orphan.

Real SQLite and filesystem, no CoreUI, plugins, network or file deletion.
"""
from __future__ import annotations

import sqlite3

import pytest

from cifereca.core.missing_placement_retirement import (
    prepare_missing_placement_retirement,
    retire_missing_placement,
)
from cifereca.storage import Storage


@pytest.fixture
def rig(tmp_path):
    storage = Storage(tmp_path / "db" / "state.sqlite3")
    storage.identity_put({
        "identity_id": "alice", "label": "Alice", "directory_name": "alice",
    })
    storage.identity_put({
        "identity_id": "bob", "label": "Bob", "directory_name": "bob",
    })
    root = tmp_path / "library"
    directory = root / "alice" / "kobo"
    directory.mkdir(parents=True)
    path = directory / "gone.epub"
    placement = storage.placement_put({
        "identity_id": "alice", "artifact_id": "sha256:" + "a" * 64,
        "path": str(path), "state": "missing", "provider_id": "kobo",
        "source_item_id": "edition",
    })
    yield storage, root, path, placement
    storage.close()


def prepare(rig, *, placement=None, identity="alice", root=None):
    storage, library_root, _path, pid = rig
    return prepare_missing_placement_retirement(
        storage, placement_id=placement or pid, identity_id=identity,
        library_root=root or library_root,
    )


def retire(rig, token, *, confirmed=True, placement=None, identity="alice"):
    storage, root, _path, pid = rig
    return retire_missing_placement(
        storage, placement_id=placement or pid, identity_id=identity,
        library_root=root, plan_token=token, confirmed=confirmed,
    )


def test_explicit_prepare_commit_is_sqlite_only_and_idempotent(rig):
    storage, _root, path, pid = rig
    initial = storage.placement_get(pid)
    plan = prepare(rig)
    assert plan["status"] == "ready"
    assert len(plan["plan_token"]) == 64
    assert str(path) not in str(plan)
    assert storage.placement_get(pid) == initial
    assert retire(rig, plan["plan_token"], confirmed=False)["reason"] == "confirmation_required"
    assert storage.placement_get(pid) == initial
    assert retire(rig, plan["plan_token"]) == {
        "status": "retired", "reason": "orphan_missing",
    }
    assert storage.placement_get(pid) is None
    assert not path.exists()
    assert retire(rig, plan["plan_token"])["reason"] == "placement_absent"


def test_missing_confirmation_and_bad_token_cannot_delete(rig):
    storage, _root, _path, pid = rig
    assert retire(rig, "", confirmed=True)["reason"] == "invalid_plan_token"
    assert retire(rig, "a" * 64, confirmed=False)["reason"] == "confirmation_required"
    assert retire(rig, "0" * 64)["reason"] == "stale_plan"
    assert storage.placement_get(pid) is not None


@pytest.mark.parametrize("kind", ["file", "dangling_link", "directory"])
def test_physical_return_blocks_preparation_and_commit(rig, kind):
    storage, _root, path, pid = rig
    token = prepare(rig)["plan_token"]
    if kind == "file":
        path.write_bytes(b"returned")
    elif kind == "dangling_link":
        path.symlink_to(path.parent / "not-present.epub")
    else:
        path.mkdir()
    assert prepare(rig)["reason"] == "path_exists"
    assert retire(rig, token)["reason"] == "path_exists"
    assert storage.placement_get(pid) is not None


def test_stale_plan_after_row_edit_even_same_updated_second(rig):
    storage, _root, path, pid = rig
    token = prepare(rig)["plan_token"]
    before = storage.placement_get(pid)
    with storage._lock, storage._connect() as db:
        db.execute(
            "UPDATE placements SET metadata_json=? WHERE placement_id=?",
            ('{"new_receipt":"yes"}', pid),
        )
    assert retire(rig, token)["reason"] == "stale_plan"
    assert storage.placement_get(pid)["updated_at"] == before["updated_at"]
    assert not path.exists()


def test_source_references_and_artifact_survive_retirement(rig):
    storage, _root, _path, pid = rig
    # A shared artifact is not evidence for a filesystem placement.
    storage.artifact_put({
        "artifact_id": "sha256:" + "a" * 64,
        "sha256": "a" * 64,
    })
    token = prepare(rig)["plan_token"]
    assert retire(rig, token)["status"] == "retired"
    assert storage.artifact_get("sha256:" + "a" * 64) is not None


def test_active_catalogue_and_backup_keep_sqlite_history(rig):
    storage, _root, path, pid = rig
    storage.catalog_entry_put({
        "catalog_entry_id": "catalog:linked", "placement_id": pid,
        "artifact_id": "sha256:" + "a" * 64, "identity_id": "alice",
        "provider_id": "kobo", "item_id": "edition", "title": "Edition",
        "local_state": "missing", "metadata": {},
    })
    assert prepare(rig)["reason"] == "dependent_references"
    assert storage.placement_get(pid) is not None
    assert storage.catalog_entry_get("catalog:linked") is not None
    with storage._lock, storage._connect() as db:
        db.execute("DELETE FROM catalog_entries WHERE catalog_entry_id='catalog:linked'")
    token = prepare(rig)["plan_token"]
    storage.backup_put({
        "provider_id": "kobo", "credential_id": "old",
        "item_id": "historic", "identity_id": "alice",
        "title": "History", "status": "reconcile_required",
        "local_path": str(path),
    })
    assert retire(rig, token)["reason"] == "dependent_references"
    assert storage.placement_get(pid) is not None


def test_checkpoint_referencing_only_placement_id_blocks_retirement(rig):
    storage, _root, path, pid = rig
    plan = prepare(rig)
    assert plan["status"] == "ready"
    # A restart marker need not embed the old path. Its exact ID is enough
    # to make retirement unsafe, even when the leaf is still absent.
    storage.metadata_set("core.synthetic.pending-placement", {
        "pending": {"placement_id": pid},
    })
    assert prepare(rig)["reason"] == "dependent_references"
    assert retire(rig, plan["plan_token"])["reason"] == "dependent_references"
    assert storage.placement_get(pid) is not None
    assert not path.exists()


def test_scope_parent_missing_and_cross_profile_are_blocked(rig, tmp_path):
    storage, root, path, pid = rig
    assert prepare(rig, identity="bob")["reason"] == "identity_mismatch"
    assert retire(rig, prepare(rig)["plan_token"], identity="bob")["reason"] == "identity_mismatch"
    path.parent.rmdir()
    assert prepare(rig)["reason"] == "parent_or_root_unavailable"
    assert storage.placement_get(pid) is not None
    assert prepare(rig, root=tmp_path / "absent")["status"] == "blocked"


def test_sqlite_abort_rolls_back_retirement(rig):
    storage, _root, _path, pid = rig
    token = prepare(rig)["plan_token"]
    with storage._lock, storage._connect() as db:
        db.execute(
            "CREATE TRIGGER deny_missing_delete BEFORE DELETE ON placements "
            "BEGIN SELECT RAISE(ABORT, 'blocked by test'); END"
        )
    assert retire(rig, token)["reason"] == "transaction_failed"
    assert storage.placement_get(pid) is not None
    with storage._lock, storage._connect() as db:
        db.execute("DROP TRIGGER deny_missing_delete")
    assert retire(rig, token)["status"] == "retired"


def test_concurrent_stale_prepare_cannot_retire_reactivated_placement(rig):
    storage, _root, path, pid = rig
    token = prepare(rig)["plan_token"]
    path.write_bytes(b"new bytes")
    storage.placement_put({
        "placement_id": pid, "identity_id": "alice",
        "path": str(path), "state": "present",
        "artifact_id": "sha256:" + "f" * 64,
        "provider_id": "kobo", "source_item_id": "edition",
    })
    assert retire(rig, token)["reason"] == "not_missing"
    assert storage.placement_get(pid)["state"] == "present"
    assert path.read_bytes() == b"new bytes"



def test_corrupt_shared_identity_directory_never_authorizes_sql_retirement(rig):
    """A legacy DB with two profile IDs pointing to one folder fails closed."""
    storage, root, path, pid = rig
    previous = prepare(rig)
    assert previous["status"] == "ready"

    # The current schema disallows this corruption; only a disposable
    # fixture removes its UNIQUE index to emulate an old/imported file.
    with storage._lock, storage._connect() as db:
        db.execute("DROP INDEX uq_identity_directory_name")
        db.execute(
            "UPDATE identities SET directory_name='alice' WHERE identity_id='bob'"
        )
    assert prepare(rig) == {
        "status": "blocked", "reason": "ambiguous_identity_directory",
    }
    assert retire(rig, previous["plan_token"]) == {
        "status": "blocked", "reason": "ambiguous_identity_directory",
    }
    from cifereca.core.missing_placement_readiness import (
        preview_missing_placement_retirement,
    )
    preview = preview_missing_placement_retirement(
        storage, placement_id=pid, identity_id="alice", library_root=root,
    )
    assert preview["status"] == "blocked"
    assert preview["reason"] == "ambiguous_identity_directory"
    assert storage.placement_get(pid)["state"] == "missing"
    assert not path.exists()

    # Repairing the ownership registry makes the original orphan eligible
    # again, but never without a fresh explicit confirmation.
    with storage._lock, storage._connect() as db:
        db.execute(
            "UPDATE identities SET directory_name='bob' WHERE identity_id='bob'"
        )
    assert prepare(rig)["status"] == "ready"
    assert storage.placement_get(pid)["state"] == "missing"



def test_changed_owner_directory_revokes_prior_missing_retirement_token(rig):
    storage, _root, path, pid = rig
    token = prepare(rig)["plan_token"]
    with storage._lock, storage._connect() as db:
        db.execute(
            "UPDATE identities SET directory_name='alice-renamed' "
            "WHERE identity_id='alice'"
        )
    # An old, fully valid plan cannot be reused after an owner moves roots.
    result = retire(rig, token)
    assert result["status"] == "blocked"
    assert result["reason"] == "path_out_of_scope"
    assert storage.placement_get(pid)["state"] == "missing"
    assert not path.exists()



@pytest.mark.parametrize("swap_on_probe", [1, 2])
def test_root_replaced_after_prepare_never_retires_old_sqlite_placement(
    rig, tmp_path, monkeypatch, swap_on_probe,
):
    """Absence in a replacement mount is not the original root's absence."""
    import shutil
    from cifereca.core import missing_placement_retirement as module

    storage, root, path, pid = rig
    token = prepare(rig)["plan_token"]
    original = storage.placement_get(pid)
    original_mount = tmp_path / "library-original-mount"
    real = module._physical_absence
    calls = []

    def swap_mount(owner, stored_path):
        calls.append((owner, stored_path))
        if len(calls) == swap_on_probe:
            root.rename(original_mount)
            (root / "alice" / "kobo").mkdir(parents=True)
        return real(owner, stored_path)

    try:
        with monkeypatch.context() as patch:
            patch.setattr(module, "_physical_absence", swap_mount)
            outcome = retire(rig, token)
        assert outcome == {"status": "blocked", "reason": "root_changed"}
        assert storage.placement_get(pid) == original
        assert not path.exists()
    finally:
        if original_mount.exists():
            shutil.rmtree(root)
            original_mount.rename(root)

    # The original directory inode is now back, but the earlier failed
    # attempt never deleted a row or acquired rights over replacement bytes.
    assert storage.placement_get(pid) == original
    assert retire(rig, token)["status"] == "retired"



def test_profile_directory_swapped_after_prepare_invalidates_missing_retirement(
    rig, tmp_path,
):
    """Root inode alone does not attest a profile folder's mount identity."""
    import shutil

    storage, root, _path, pid = rig
    token = prepare(rig)["plan_token"]
    original = storage.placement_get(pid)
    owner = root / "alice"
    moved = tmp_path / "old-alice-mount"
    root_inode = root.stat().st_ino

    owner.rename(moved)
    (owner / "kobo").mkdir(parents=True)
    try:
        result = retire(rig, token)
        assert result == {"status": "blocked", "reason": "stale_plan"}
        assert storage.placement_get(pid) == original
        assert root.stat().st_ino == root_inode
    finally:
        shutil.rmtree(owner)
        moved.rename(owner)

    # An unchanged SQLite orphan can still be retired after restoring
    # the exact original profile directory and explicit confirmation.
    assert retire(rig, token)["status"] == "retired"


@pytest.mark.parametrize("swap_on_probe", [1, 2])
def test_profile_directory_swap_during_commit_preserves_missing_placement(
    rig, tmp_path, monkeypatch, swap_on_probe,
):
    """A same-root subdirectory swap must not turn absence into DELETE authority."""
    import shutil
    from cifereca.core import missing_placement_retirement as module

    storage, root, _path, pid = rig
    token = prepare(rig)["plan_token"]
    original = storage.placement_get(pid)
    owner = root / "alice"
    moved = tmp_path / "alice-old-profile"
    actual = module._directory_signature
    calls = []

    def swap_before_directory_probe(path):
        calls.append(path)
        if len(calls) == swap_on_probe:
            owner.rename(moved)
            (owner / "kobo").mkdir(parents=True)
        return actual(path)

    try:
        with monkeypatch.context() as patch:
            patch.setattr(module, "_directory_signature", swap_before_directory_probe)
            result = retire(rig, token)
        # When the directory has already changed before _evidence, the
        # immutable plan fingerprint is stale; when it changes just before
        # DELETE, the final no-follow directory recheck blocks it.
        assert result["status"] == "blocked"
        assert result["reason"] == (
            "stale_plan" if swap_on_probe == 1
            else "identity_directory_changed"
        )
        assert storage.placement_get(pid) == original
        assert root.is_dir()
    finally:
        if moved.exists():
            shutil.rmtree(owner)
            moved.rename(owner)

    assert retire(rig, token)["status"] == "retired"
