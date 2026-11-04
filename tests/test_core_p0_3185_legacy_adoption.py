"""P0 #3185: exact-source legacy label adoption is operator-only and non-destructive.

Synthetic SQLite v14 + files only. Never invokes a supplier, ADEPT or unlink.
"""
import json
import sqlite3

import pytest

from cifereca.core.canonical_legacy_adoption import (
    adopt_verified_legacy_placement,
)
from cifereca.locking import InterProcessLock
from tests.test_core_p0_3185_canonical_backup_relink import (
    rig, backup,
)
from tests.test_core_p0_3185_legacy_scope_review import _mark_legacy
from tests.test_core_p0_3719_prepare_duplicate import _journal, _catalogue
from tests.test_core_p0_3171_atomic_publication_v14 import (
    BYTES, rows,
)


def _adopt(rig, *, action="preview", confirmed=False, shared_lock=None):
    storage, root, _paths, pubs, _lock = rig
    return adopt_verified_legacy_placement(
        storage, library_root=root,
        candidate_placement_id=pubs[1].placement_id,
        keeper_placement_id=pubs[0].placement_id,
        action=action, confirmed=confirmed, shared_lock=shared_lock,
    )


def _placement(rig):
    storage, _root, _paths, pubs, _lock = rig
    return storage.placement_get(pubs[1].placement_id)


def _commit(rig):
    *_, lock_path = rig
    with InterProcessLock(lock_path) as lock:
        return _adopt(rig, action="commit", confirmed=True, shared_lock=lock)


def test_proven_legacy_label_is_adopted_without_mutating_any_file_or_receipt(rig):
    storage, _root, paths, pubs, _lock = rig
    _mark_legacy(rig)
    checkpoint = _journal(rig)
    backup(rig)
    _catalogue(rig)
    with storage._lock, storage._connect() as db:
        original_checkpoint = db.execute(
            "SELECT metadata_json FROM acquisition_ledger WHERE acquisition_id=?",
            (checkpoint,),
        ).fetchone()[0]
        original_origins = db.execute(
            "SELECT origin_link_id,evidence_ref FROM origin_links "
            "ORDER BY origin_link_id"
        ).fetchall()
        original_sources = db.execute(
            "SELECT source_reference_id,evidence_ref FROM source_references "
            "ORDER BY source_reference_id"
        ).fetchall()
    before = rows(storage)
    preview = _adopt(rig)
    assert preview.status == "review_required"
    assert preview.completed_path_receipts == 1
    assert preview.may_delete is False
    assert (preview.catalogue_references, preview.backup_references) == (1, 1)
    assert _placement(rig)["provider_id"] == "seller-b"

    result = _commit(rig)
    assert result.status == "migrated"
    assert result.may_delete is False
    assert _placement(rig)["provider_id"] is None
    assert _placement(rig)["source_item_id"] is None
    assert rows(storage) == before
    with storage._lock, storage._connect() as db:
        assert db.execute(
            "SELECT metadata_json FROM acquisition_ledger WHERE acquisition_id=?",
            (checkpoint,),
        ).fetchone()[0] == original_checkpoint
        assert db.execute(
            "SELECT origin_link_id,evidence_ref FROM origin_links "
            "ORDER BY origin_link_id"
        ).fetchall() == original_origins
        assert db.execute(
            "SELECT source_reference_id,evidence_ref FROM source_references "
            "ORDER BY source_reference_id"
        ).fetchall() == original_sources
    assert storage.catalog_entry_get("catalogue:seller-b")["placement_id"] == pubs[1].placement_id
    assert all(path.read_bytes() == BYTES for path in paths)
    again = _commit(rig)
    assert again.status == "blocked"
    assert again.reason == "already_canonical"
    assert all(path.exists() for path in paths)


def test_no_receipt_is_not_an_adoption_credential(rig):
    _mark_legacy(rig)
    result = _adopt(rig)
    assert result.status == "blocked"
    assert result.reason == "receipt_unverified"
    assert _placement(rig)["provider_id"] == "seller-b"


def test_commit_requires_independently_held_lock_and_explicit_confirmation(rig):
    storage, _root, paths, pubs, lock_path = rig
    _mark_legacy(rig)
    _journal(rig)
    with pytest.raises(ValueError, match="confirmation_and_lock_required"):
        _adopt(rig, action="commit", confirmed=True,
               shared_lock=InterProcessLock(lock_path))
    with InterProcessLock(lock_path) as lock:
        with pytest.raises(ValueError, match="confirmation_and_lock_required"):
            _adopt(rig, action="commit", confirmed=False, shared_lock=lock)
    assert _placement(rig)["provider_id"] == "seller-b"
    assert all(path.exists() for path in paths)


@pytest.mark.parametrize("defect", [
    "modified_bytes", "symlink", "missing_file", "opaque_metadata",
    "wrong_backup_account", "other_path_receipt", "foreign_catalogue",
])
def test_unverified_path_or_refs_never_change_provider_binding(rig, defect):
    storage, _root, paths, pubs, _lock = rig
    _mark_legacy(rig)
    checkpoint = _journal(rig)
    if defect == "modified_bytes":
        paths[1].write_bytes(BYTES[:-1] + b"X")
    elif defect == "symlink":
        paths[1].unlink()
        paths[1].symlink_to(paths[0])
    elif defect == "missing_file":
        paths[1].unlink()
    elif defect == "opaque_metadata":
        storage.metadata_set("synthetic.opaque.path", str(paths[1]))
    elif defect == "wrong_backup_account":
        backup(rig, account="wrong-account")
    elif defect == "other_path_receipt":
        with storage._lock, storage._connect() as db:
            receipt = json.loads(db.execute(
                "SELECT metadata_json FROM acquisition_ledger "
                "WHERE acquisition_id=?", (checkpoint,),
            ).fetchone()[0])
            db.execute(
                "INSERT INTO acquisition_ledger("
                "acquisition_id,provider_id,credential_id,account_id,item_id,"
                "identity_id,status,artifact_id,metadata_json,created_at,updated_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                ("other-stage", "seller-b", "card-b", "different-buyer",
                 "book", "A", "retryable_error", pubs[1].artifact_id,
                 json.dumps({"nested": {"path": receipt["path"]}}), 1, 1),
            )
    else:
        storage.catalog_entry_put({
            "catalog_entry_id": "foreign:catalogue",
            "artifact_id": pubs[1].artifact_id,
            "placement_id": pubs[0].placement_id,
            "identity_id": "A", "provider_id": "seller-b",
            "item_id": "book", "title": "Other",
            "metadata": {"path": str(paths[1])},
        })
    before = rows(storage)
    assert _adopt(rig).status == "blocked"
    assert _commit(rig).status == "blocked"
    assert rows(storage) == before
    assert _placement(rig)["provider_id"] == "seller-b"


def test_sql_abort_rolls_back_the_only_provider_binding_update(rig):
    storage, _root, paths, pubs, _lock = rig
    _mark_legacy(rig)
    _journal(rig)
    assert _adopt(rig).status == "review_required"
    with storage._lock, storage._connect() as db:
        db.execute(
            "CREATE TRIGGER synthetic_denied_adoption "
            "BEFORE UPDATE OF provider_id ON placements "
            "WHEN OLD.placement_id=? "
            "BEGIN SELECT RAISE(ABORT,'synthetic_abort'); END".replace(
                "WHEN OLD.placement_id=?", "WHEN OLD.placement_id='"
                + pubs[1].placement_id + "'"
            )
        )
    result = _commit(rig)
    assert result.status == "blocked"
    assert result.reason == "inspection_or_commit_unavailable"
    assert _placement(rig)["provider_id"] == "seller-b"
    assert all(path.read_bytes() == BYTES for path in paths)


def test_changed_owner_or_borrower_scope_is_not_a_migration(rig):
    storage, _root, paths, pubs, _lock = rig
    _mark_legacy(rig)
    _journal(rig)
    assert _adopt(rig).status == "review_required"
    with storage._lock, storage._connect() as db:
        db.execute(
            "UPDATE provider_connections SET enabled=0 "
            "WHERE provider_id='seller-b' AND credential_id='card-b'"
        )
    result = _commit(rig)
    assert result.status == "blocked"
    assert result.reason == "receipt_unverified"
    assert _placement(rig)["provider_id"] == "seller-b"
    assert all(path.read_bytes() == BYTES for path in paths)
