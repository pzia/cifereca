"""#3185: one Core coordinator preserves origins and survives partial commits."""
import json
import sqlite3
from pathlib import Path

import pytest

from cifereca.core.canonical_dedup_execution import execute_canonical_duplicate
from cifereca.core.canonical_recovery import _scope, _stage_id
from cifereca.locking import InterProcessLock
from tests.test_core_p0_3171_atomic_publication_v14 import (
    BYTES, SHA, new_source, publish, register, rows, store, target,
)


@pytest.fixture
def rig(store, tmp_path, monkeypatch):
    root = tmp_path / "EBooks"
    sources, paths, pubs = [], [], []
    for identity, provider, credential, account, filename in (
        ("A", "seller-a", "card-a", "borrower-a", "first.pdf"),
        ("A", "seller-b", "card-b", "borrower-b", "second.pdf"),
        ("B", "seller-c", "card-c", "borrower-c", "foreign.pdf"),
    ):
        source, snapshot = new_source(
            identity, provider=provider, credential=credential,
            account=account, edition="book",
        )
        register(store, identity, provider, credential, account=account)
        path = target(tmp_path, identity, filename)
        sources.append(source)
        paths.append(path)
        pubs.append(publish(store, root, path, source, snapshot))
    lock_path = tmp_path / "locks" / "acquisition.lock"
    monkeypatch.setattr(
        "cifereca.core.canonical_recovery.acquisition_lock_file",
        lambda: lock_path,
    )
    return store, root, paths, pubs, sources, lock_path


def attach_all_references(rig, *, staged=False):
    storage, _root, paths, pubs, sources, _lock = rig
    storage.catalog_entry_put({
        "catalog_entry_id": "book-b-notice",
        "artifact_id": pubs[1].artifact_id,
        "placement_id": pubs[1].placement_id,
        "identity_id": "A",
        "provider_id": "seller-b",
        "item_id": "book",
        "title": "Synthetic independent purchase",
        "metadata": {"path": str(paths[1]), "source": "seller-b"},
    })
    with storage._lock, storage._connect() as db:
        db.execute(
            "INSERT INTO backup_items(provider_id,credential_id,item_id,"
            "identity_id,account_id,title,status,sha256,size,local_path,updated_at)"
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            ("seller-b", "card-b", "book", "A", "borrower-b", "Synthetic",
             "complete", SHA, len(BYTES), str(paths[1]), 1),
        )
        source = sources[1]
        stage = {
            "schema": "canonical.completed-delivery.v1",
            "source": _scope(source),
            "offer_id": "synthetic-offer",
            "path": str(paths[1]),
            "sha256": SHA,
            "receipt": "synthetic-completed-delivery",
            "adept_receipt": "",
        }
        db.execute(
            "INSERT INTO acquisition_ledger(acquisition_id,provider_id,"
            "credential_id,account_id,item_id,identity_id,status,"
            "artifact_id,metadata_json,created_at,updated_at)"
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (_stage_id(source, stage["offer_id"]), "seller-b", "card-b",
             "borrower-b", "book", "A",
             "canonical_receipt_staged" if staged else "complete",
             pubs[1].artifact_id, json.dumps(stage), 1, 1),
        )


def run(rig, *, retire=False, keeper=None):
    storage, root, _paths, pubs, _sources, lock_path = rig
    with InterProcessLock(lock_path) as lock:
        return execute_canonical_duplicate(
            storage, library_root=root,
            candidate_placement_id=pubs[1].placement_id,
            keeper_placement_id=keeper or pubs[0].placement_id,
            shared_lock=lock, retire=retire,
        )


def current_refs(rig):
    storage, _root, _paths, _pubs, sources, _lock = rig
    with storage._lock, storage._connect() as db:
        return (
            db.execute(
                "SELECT placement_id,metadata_json FROM catalog_entries "
                "WHERE catalog_entry_id='book-b-notice'"
            ).fetchone(),
            db.execute(
                "SELECT local_path FROM backup_items WHERE credential_id='card-b'"
            ).fetchone(),
            db.execute(
                "SELECT metadata_json FROM acquisition_ledger WHERE provider_id='seller-b'"
            ).fetchone(),
        )


def test_relink_only_preserves_two_physical_copies_and_every_origin(rig):
    storage, _root, paths, pubs, _sources, _lock = rig
    attach_all_references(rig)
    original = rows(storage)
    report = run(rig)
    assert (report.status, report.reason) == (
        "ready", "all_references_relinked",
    )
    assert (report.catalogue_relinked, report.backup_relinked,
            report.receipt_relinked) == (1, 1, 1)
    assert not report.removed_file
    catalogue, backup, receipt = current_refs(rig)
    assert catalogue[0] == pubs[0].placement_id
    assert json.loads(catalogue[1])["path"] == str(paths[0])
    assert backup == (str(paths[0]),)
    assert json.loads(receipt[0])["path"] == str(paths[0])
    assert run(rig).status == "ready"
    assert rows(storage) == original == (1, 3, 3, 3)
    assert all(path.read_bytes() == BYTES for path in paths)


def test_explicit_retirement_after_relinks_preserves_origins_and_keeper(rig):
    storage, _root, paths, pubs, _sources, _lock = rig
    attach_all_references(rig)
    report = run(rig, retire=True)
    assert (report.status, report.reason) == ("completed", "verified")
    assert (report.catalogue_relinked, report.backup_relinked,
            report.receipt_relinked) == (1, 1, 1)
    assert report.retired_placement and report.removed_file
    assert not report.cleanup_pending
    assert storage.placement_get(pubs[1].placement_id) is None
    assert not paths[1].exists()
    assert all(path.read_bytes() == BYTES for path in (paths[0], paths[2]))
    assert rows(storage) == (1, 2, 3, 3)
    assert current_refs(rig)[1] == (str(paths[0]),)


def test_foreign_profile_and_replaced_file_refuse_without_any_relink(rig):
    storage, _root, paths, pubs, _sources, _lock = rig
    attach_all_references(rig)
    before = current_refs(rig)
    bad_scope = run(rig, retire=True, keeper=pubs[2].placement_id)
    assert (bad_scope.status, bad_scope.reason) == ("blocked", "scope_mismatch")
    paths[1].write_bytes(BYTES[:-1] + b"X")
    bad_sha = run(rig, retire=True)
    assert (bad_sha.status, bad_sha.reason) == (
        "blocked", "physical_evidence_changed",
    )
    assert current_refs(rig) == before
    assert all(path.exists() for path in paths)


def test_unknown_active_reference_blocks_destructive_step(rig):
    storage, _root, paths, pubs, _sources, _lock = rig
    storage.metadata_set("unknown.future.operational.path", str(paths[1]))
    result = run(rig, retire=True)
    assert (result.status, result.reason) == ("blocked", "active_references")
    assert storage.placement_get(pubs[1].placement_id)["state"] == "present"
    assert paths[1].read_bytes() == BYTES


def test_staged_checkpoint_fails_closed_and_does_not_unlink(rig):
    storage, _root, paths, pubs, _sources, _lock = rig
    attach_all_references(rig, staged=True)
    result = run(rig, retire=True)
    assert (result.status, result.reason) == (
        "blocked", "reference_relink_failed",
    )
    assert result.catalogue_relinked == result.backup_relinked == 1
    assert result.receipt_relinked == 0
    assert storage.placement_get(pubs[1].placement_id)["state"] == "present"
    assert all(path.read_bytes() == BYTES for path in paths)
    # Relinks already committed are safe, and may be retried idempotently.
    assert current_refs(rig)[1] == (str(paths[0]),)


def test_sql_abort_between_relinks_keeps_both_files_for_retry(rig):
    storage, _root, paths, pubs, _sources, _lock = rig
    attach_all_references(rig)
    with storage._lock, storage._connect() as db:
        db.execute(
            "CREATE TRIGGER forbid_legacy_relink BEFORE UPDATE OF local_path "
            "ON backup_items BEGIN "
            "SELECT RAISE(ABORT,'synthetic_backup_sql_abort'); END"
        )
    first = run(rig, retire=True)
    assert (first.status, first.reason) == (
        "blocked", "reference_relink_failed",
    )
    assert first.catalogue_relinked == 1 and first.backup_relinked == 0
    assert all(path.read_bytes() == BYTES for path in paths)
    assert storage.placement_get(pubs[1].placement_id)["state"] == "present"
    with storage._lock, storage._connect() as db:
        db.execute("DROP TRIGGER forbid_legacy_relink")
    second = run(rig, retire=True)
    assert second.status == "completed"
    assert not paths[1].exists()
    assert rows(storage) == (1, 2, 3, 3)


def test_explicit_recovery_after_filesystem_denial(rig, monkeypatch):
    storage, _root, paths, pubs, _sources, _lock = rig
    original_unlink = Path.unlink

    def refuse(path, *args, **kwargs):
        if path == paths[1]:
            raise PermissionError("synthetic_fs_denied")
        return original_unlink(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", refuse)
        outcome = run(rig, retire=True)
    assert outcome.status == "cleanup_pending"
    assert outcome.retired_placement and outcome.cleanup_pending
    assert all(path.read_bytes() == BYTES for path in paths)
    assert storage.placement_get(pubs[1].placement_id)["state"] == "dedup_cleanup_pending"
    assert run(rig).reason == "cleanup_requires_explicit_recovery"
    recovered = run(rig, retire=True)
    assert recovered.status == "completed"
    assert recovered.reason == "recovered"
    assert not recovered.cleanup_pending and recovered.removed_file
    assert storage.placement_get(pubs[1].placement_id) is None
    assert paths[0].read_bytes() == BYTES


def test_invalid_lock_does_not_mutate_anything(rig):
    storage, root, paths, pubs, _sources, lock_path = rig
    with pytest.raises(ValueError, match="canonical_dedup_execution_invalid_request"):
        execute_canonical_duplicate(
            storage, library_root=root,
            candidate_placement_id=pubs[1].placement_id,
            keeper_placement_id=pubs[0].placement_id,
            shared_lock=InterProcessLock(lock_path), retire=True,
        )
    assert all(path.read_bytes() == BYTES for path in paths)
