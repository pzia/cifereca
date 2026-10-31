"""#3185: an explicit Core supervisor never mistakes preview for unlink consent."""
import json
from pathlib import Path

import pytest

from cifereca.core import canonical_dedup_supervisor as core
from cifereca.core.canonical_dedup_retirement import CanonicalRetirementResult
from cifereca.locking import InterProcessLock
from tests.test_core_p0_3171_atomic_publication_v14 import BYTES, rows, store
from tests.test_core_p0_3185_canonical_backup_relink import rig, backup
from tests.test_core_p0_3719_prepare_duplicate import _catalogue, _journal


def inspect(rig, *, action="preview", candidate=None, keeper=None,
            confirmed=False, live=True):
    storage, root, _paths, pubs, lock_path = rig
    if action == "preview":
        return core.supervise_canonical_duplicate(
            storage, library_root=root,
            candidate_placement_id=candidate or pubs[1].placement_id,
            keeper_placement_id=keeper or pubs[0].placement_id,
            action=action,
        )
    lock = InterProcessLock(lock_path)
    if not live:
        return core.supervise_canonical_duplicate(
            storage, library_root=root,
            candidate_placement_id=candidate or pubs[1].placement_id,
            keeper_placement_id=keeper or pubs[0].placement_id,
            action=action, confirmed=confirmed, shared_lock=lock,
        )
    with lock:
        return core.supervise_canonical_duplicate(
            storage, library_root=root,
            candidate_placement_id=candidate or pubs[1].placement_id,
            keeper_placement_id=keeper or pubs[0].placement_id,
            action=action, confirmed=confirmed, shared_lock=lock,
        )


def test_preview_never_mutates_even_when_a_duplicate_is_physically_verified(rig):
    storage, _root, paths, pubs, _ = rig
    before = rows(storage)
    outcome = inspect(rig)
    assert (outcome.action, outcome.status, outcome.reason) == (
        "preview", "preview_ready", "verified_no_active_references",
    )
    assert outcome.finished is False
    assert rows(storage) == before == (1, 3, 3, 3)
    assert storage.placement_get(pubs[1].placement_id)["state"] == "present"
    assert all(path.read_bytes() == BYTES for path in paths)


@pytest.mark.parametrize("live,confirmed", [
    (False, False), (False, True), (True, False),
])
def test_commit_requires_explicit_confirmation_and_live_shared_lock(
    rig, live, confirmed,
):
    storage, _root, paths, pubs, _ = rig
    with pytest.raises(ValueError, match="confirmation_and_lock_required"):
        inspect(rig, action="commit", confirmed=confirmed, live=live)
    assert rows(storage) == (1, 3, 3, 3)
    assert all(path.read_bytes() == BYTES for path in paths)


def test_supervised_commit_migrates_all_three_reference_classes_and_postchecks(rig):
    storage, _root, paths, pubs, _ = rig
    _catalogue(rig)
    backup(rig)
    receipt = _journal(rig)
    before = rows(storage)
    preview = inspect(rig)
    assert preview.status == "preview_blocked"
    assert preview.reason == "active_references"
    assert all(p.exists() for p in paths)

    completed = inspect(rig, action="commit", confirmed=True)
    assert completed.status == "retired" and completed.finished
    assert completed.removed_file
    assert (completed.catalogue_relinked, completed.backup_relinked,
            completed.receipt_relinked) == (1, 1, 1)
    assert rows(storage) == (1, 2, 3, 3)
    assert before == (1, 3, 3, 3)
    assert storage.placement_get(pubs[1].placement_id) is None
    assert storage.catalog_entry_get("catalogue:seller-b")["placement_id"] == pubs[0].placement_id
    assert next(x for x in storage.backup_list()
                if x["credential_id"] == "card-b")["local_path"] == str(paths[0])
    with storage._lock, storage._connect() as db:
        raw = db.execute(
            "SELECT metadata_json FROM acquisition_ledger WHERE acquisition_id=?",
            (receipt,),
        ).fetchone()[0]
    assert json.loads(raw)["path"] == str(paths[0])
    assert not paths[1].exists()
    assert paths[0].read_bytes() == paths[2].read_bytes() == BYTES


def test_unmigratable_staged_receipt_blocks_before_any_unlink(rig):
    storage, _root, paths, pubs, _ = rig
    _catalogue(rig)
    backup(rig)
    receipt = _journal(rig, status="canonical_receipt_staged")
    found = inspect(rig, action="commit", confirmed=True)
    assert found.status == "blocked"
    assert found.reason == "relink_refused"
    # Successful earlier relinks are idempotent, not a cancellation of B.
    assert storage.catalog_entry_get("catalogue:seller-b")["placement_id"] == pubs[0].placement_id
    assert storage.placement_get(pubs[1].placement_id)["state"] == "present"
    assert all(path.read_bytes() == BYTES for path in paths)
    with storage._lock, storage._connect() as db:
        data = json.loads(db.execute(
            "SELECT metadata_json FROM acquisition_ledger WHERE acquisition_id=?",
            (receipt,),
        ).fetchone()[0])
    assert data["path"] == str(paths[1])


def test_unknown_operational_reference_blocks_retirement_even_after_prepare(rig):
    storage, _root, paths, pubs, _ = rig
    storage.metadata_set("synthetic.unmigrated.path", str(paths[1]))
    result = inspect(rig, action="commit", confirmed=True)
    assert result.status == "blocked" and result.reason == "remaining_dependencies"
    assert storage.placement_get(pubs[1].placement_id)["state"] == "present"
    assert rows(storage) == (1, 3, 3, 3)
    assert all(path.read_bytes() == BYTES for path in paths)


def test_other_profile_equal_sha_cannot_be_selected_as_keeper(rig):
    storage, _root, paths, pubs, _ = rig
    rejected = inspect(rig, action="commit", confirmed=True,
                       keeper=pubs[2].placement_id)
    assert rejected.status == "blocked" and rejected.reason == "pair_unverified"
    assert rows(storage) == (1, 3, 3, 3)
    assert all(path.read_bytes() == BYTES for path in paths)


def test_failed_unlink_stays_pending_until_explicit_recovery(rig, monkeypatch):
    storage, _root, paths, pubs, _ = rig
    actual_unlink = Path.unlink
    with monkeypatch.context() as patch:
        def refuse(path, *args, **kwargs):
            if path == paths[1]:
                raise PermissionError("synthetic_unlink_refused")
            return actual_unlink(path, *args, **kwargs)
        patch.setattr(Path, "unlink", refuse)
        pending = inspect(rig, action="commit", confirmed=True)
    assert pending.status == "cleanup_pending"
    assert not pending.removed_file and not pending.finished
    assert storage.placement_get(pubs[1].placement_id)["state"] == "dedup_cleanup_pending"
    assert all(path.read_bytes() == BYTES for path in paths)
    resumed = inspect(rig, action="recover", confirmed=True)
    assert resumed.status == "retired" and resumed.finished
    assert resumed.removed_file
    assert storage.placement_get(pubs[1].placement_id) is None
    assert not paths[1].exists()
    assert paths[0].read_bytes() == paths[2].read_bytes() == BYTES


def test_recover_without_tombstone_is_a_noop(rig):
    storage, _root, paths, pubs, _ = rig
    result = inspect(rig, action="recover", confirmed=True)
    assert result.status == "blocked" and result.reason == "no_pending_cleanup"
    assert storage.placement_get(pubs[1].placement_id)["state"] == "present"
    assert all(path.read_bytes() == BYTES for path in paths)


def test_postcheck_must_not_claim_success_on_unconfirmed_deletion(rig, monkeypatch):
    storage, _root, paths, pubs, _ = rig
    def forged_outcome(*_args, **_kwargs):
        return CanonicalRetirementResult(True, True, False)
    monkeypatch.setattr(core, "retire_verified_canonical_duplicate",
                        forged_outcome)
    result = inspect(rig, action="commit", confirmed=True)
    assert result.status == "postcheck_failed" and not result.finished
    assert all(path.read_bytes() == BYTES for path in paths)
    assert storage.placement_get(pubs[1].placement_id)["state"] == "present"



def test_sql_interruption_between_reference_relinks_is_safe_to_retry(rig):
    storage, _root, paths, pubs, _ = rig
    _catalogue(rig)
    backup(rig)
    _journal(rig)
    with storage._lock, storage._connect() as db:
        db.execute(
            "CREATE TRIGGER stop_backup_relink BEFORE UPDATE OF local_path "
            "ON backup_items BEGIN SELECT RAISE(ABORT,'synthetic_sql_failure'); END"
        )
    failed = inspect(rig, action="commit", confirmed=True)
    assert (failed.status, failed.reason) == ("blocked", "relink_refused")
    assert storage.catalog_entry_get("catalogue:seller-b")["placement_id"] == pubs[0].placement_id
    assert next(x for x in storage.backup_list()
                if x["credential_id"] == "card-b")["local_path"] == str(paths[1])
    assert storage.placement_get(pubs[1].placement_id)["state"] == "present"
    assert all(p.read_bytes() == BYTES for p in paths)
    with storage._lock, storage._connect() as db:
        db.execute("DROP TRIGGER stop_backup_relink")
    restored = inspect(rig, action="commit", confirmed=True)
    assert restored.status == "retired" and restored.finished
    assert not paths[1].exists()
    assert paths[0].read_bytes() == paths[2].read_bytes() == BYTES
    assert rows(storage) == (1, 2, 3, 3)


def test_postcheck_rejects_orphan_file_even_when_candidate_sql_row_disappears(
    rig, monkeypatch,
):
    storage, _root, paths, pubs, _ = rig

    def forged_sql_only_retirement(_storage, **_kwargs):
        with _storage._lock, _storage._connect() as db:
            db.execute(
                "DELETE FROM placements WHERE placement_id=?",
                (pubs[1].placement_id,),
            )
        # Synthetic faulty writer: claims success after SQL DELETE only.
        return CanonicalRetirementResult(True, False, False)

    monkeypatch.setattr(core, "retire_verified_canonical_duplicate",
                        forged_sql_only_retirement)
    result = inspect(rig, action="commit", confirmed=True)
    assert result.status == "postcheck_failed"
    assert not result.finished
    assert all(path.read_bytes() == BYTES for path in paths)
    assert storage.placement_get(pubs[1].placement_id) is None
    assert storage.placement_get(pubs[0].placement_id)["state"] == "present"
