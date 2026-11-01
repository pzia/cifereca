"""#2847: no missing-Placement cleanup without exact private physical evidence.

This is a read-only, no-network Core preview. Not a DELETE API.
"""
from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from cifereca.core.missing_placement_readiness import (
    preview_missing_placement_retirement,
)
from cifereca.storage import Storage


@pytest.fixture
def case(tmp_path):
    store = Storage(tmp_path / "sqlite" / "state.sqlite3")
    store.identity_put({"identity_id": "alice", "label": "Alice", "directory_name": "alice"})
    store.identity_put({"identity_id": "bob", "label": "Bob", "directory_name": "bob"})
    root = tmp_path / "books"
    parent = root / "alice" / "kobo"
    parent.mkdir(parents=True)
    missing = parent / "gone.epub"
    pid = store.placement_put({
        "path": str(missing), "identity_id": "alice", "state": "missing",
        "artifact_id": "sha256:" + "a" * 64,
        "provider_id": "kobo", "source_item_id": "edition",
    })
    yield store, root, missing, pid
    store.close()


def _plan(case, *, identity_id="alice", placement_id=None, root=None):
    store, library_root, _, pid = case
    return preview_missing_placement_retirement(
        store, placement_id=placement_id or pid,
        identity_id=identity_id,
        library_root=root or library_root,
    )


def test_missing_unreferenced_exact_path_is_previewable_only(case):
    store, _, missing, pid = case
    before = store.placement_get(pid)
    result = _plan(case)
    assert result == {
        "status": "ready", "reason": "orphan_missing",
        "catalogue_rows": 0, "requires_confirmation": True,
    }
    assert store.placement_get(pid) == before
    assert not missing.exists()


@pytest.mark.parametrize("physical", ["file", "symlink", "directory"])
def test_reappeared_path_even_dangling_link_is_not_missing(case, physical):
    _, _, missing, _ = case
    if physical == "file":
        missing.write_bytes(b"returned book")
    elif physical == "symlink":
        missing.symlink_to(missing.parent / "not-present.epub")
    else:
        missing.mkdir()
    assert _plan(case)["reason"] == "path_exists"


def test_parent_or_root_unavailable_never_counts_as_absence(case):
    _, root, missing, _ = case
    missing.parent.rmdir()
    assert _plan(case)["reason"] == "parent_or_root_unavailable"
    assert _plan(case, root=root / "not-mounted")["reason"] == "root_unavailable"


def test_foreign_identity_and_path_outside_identity_scope_blocked(case, tmp_path):
    store, root, _, _ = case
    assert _plan(case, identity_id="bob")["reason"] == "identity_mismatch"
    assert _plan(case, identity_id="unknown")["reason"] == "unknown_identity"
    different = tmp_path / "external" / "foreign.epub"
    different.parent.mkdir()
    pid = store.placement_put({
        "path": str(different), "identity_id": "alice",
        "state": "missing", "artifact_id": "sha256:" + "a" * 64,
    })
    assert _plan(case, placement_id=pid)["reason"] == "path_out_of_scope"


def test_not_missing_and_deleted_sqlite_row_blocked(case):
    store, _, missing, pid = case
    store.placement_put({
        "placement_id": pid, "path": str(missing),
        "identity_id": "alice", "state": "unverified",
    })
    assert _plan(case)["reason"] == "not_missing"
    store.placement_delete(pid)
    assert _plan(case)["reason"] == "placement_absent"


def test_linked_local_catalogue_cannot_be_cascade_deleted_from_preview(case):
    store, _, missing, pid = case
    store.catalog_entry_put({
        "catalog_entry_id": "local:linked",
        "placement_id": pid, "artifact_id": "sha256:" + "a" * 64,
        "identity_id": "alice", "provider_id": "kobo", "item_id": "edition",
        "title": "Historical", "local_state": "missing",
        "metadata": {"path": str(missing)},
    })
    result = _plan(case)
    assert result["status"] == "blocked"
    assert result["reason"] == "dependent_references"
    assert result["catalogue_rows"] == 1
    assert store.catalog_entry_get("local:linked") is not None
    assert store.placement_get(pid) is not None


def test_historical_backup_path_is_independent_reference(case):
    store, _, missing, pid = case
    store.backup_put({
        "provider_id": "kobo", "credential_id": "old",
        "account_id": "old-account", "identity_id": "alice",
        "item_id": "historic", "title": "Archived",
        "status": "reconcile_required", "local_path": str(missing),
    })
    assert _plan(case)["reason"] == "dependent_references"
    assert store.backup_get("kobo", "old", "historic")["local_path"] == str(missing)
    assert store.placement_get(pid)["state"] == "missing"


def test_canonical_checkpoint_path_and_inaccessible_evidence_block(case):
    store, _, missing, pid = case
    store.metadata_set("core.synthetic-checkpoint", {
        "index_pending": {"path": str(missing)},
    })
    assert _plan(case)["reason"] == "dependent_references"
    assert store.placement_get(pid) is not None


def test_missing_preview_has_no_side_effects_and_no_private_path(case):
    store, _, missing, pid = case
    with store._lock, store._connect() as db:
        before = tuple(
            db.execute("SELECT * FROM " + table).fetchall()
            for table in ("placements", "catalog_entries", "artifacts",
                          "acquisition_ledger", "backup_items", "origin_links",
                          "source_references", "metadata")
        )
    output = _plan(case)
    assert str(missing) not in str(output)
    assert "a" * 64 not in str(output)
    with store._lock, store._connect() as db:
        after = tuple(
            db.execute("SELECT * FROM " + table).fetchall()
            for table in ("placements", "catalog_entries", "artifacts",
                          "acquisition_ledger", "backup_items", "origin_links",
                          "source_references", "metadata")
        )
    assert after == before
