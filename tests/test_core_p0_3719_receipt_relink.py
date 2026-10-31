"""#3719: completed-delivery journal paths may follow verified same-profile B.

The receipt and right remain unchanged. Synthetic SQLite and PDF bytes only.
"""
import json
import sqlite3

import pytest

from cifereca.core.canonical_dedup_receipt_relink import (
    relink_completed_delivery_checkpoints,
)
from cifereca.core.canonical_recovery import _scope, _stage_id
from cifereca.locking import InterProcessLock
from tests.test_core_p0_3171_atomic_publication_v14 import (
    BYTES, SHA, new_source, publish, register, rows, store, target,
)


@pytest.fixture
def rig(store, tmp_path, monkeypatch):
    root = tmp_path / "EBooks"
    first, first_snapshot = new_source(
        "A", provider="seller-a", credential="card-a", account="buyer-a",
    )
    second, second_snapshot = new_source(
        "A", provider="seller-b", credential="card-b", account="buyer-b",
    )
    foreign, foreign_snapshot = new_source(
        "B", provider="seller-c", credential="card-c", account="buyer-c",
    )
    register(store, "A", "seller-a", "card-a", account="buyer-a")
    register(store, "A", "seller-b", "card-b", account="buyer-b")
    register(store, "B", "seller-c", "card-c", account="buyer-c")
    paths = (
        target(tmp_path, "A", "first.pdf"),
        target(tmp_path, "A", "second.pdf"),
        target(tmp_path, "B", "foreign.pdf"),
    )
    publications = (
        publish(store, root, paths[0], first, first_snapshot),
        publish(store, root, paths[1], second, second_snapshot),
        publish(store, root, paths[2], foreign, foreign_snapshot),
    )
    lock_path = tmp_path / "lock" / "acquisition.lock"
    monkeypatch.setattr(
        "cifereca.core.canonical_recovery.acquisition_lock_file",
        lambda: lock_path,
    )
    return store, root, paths, publications, second, lock_path


def journal(rig, *, source=None, status="complete", receipt="synthetic-completed-delivery"):
    store, _root, paths, publications, default_source, _lock = rig
    source = source or default_source
    offer = "synthetic-offer"
    record_id = _stage_id(source, offer)
    data = {
        "schema": "canonical.completed-delivery.v1",
        "source": _scope(source), "offer_id": offer,
        "path": str(paths[1]), "sha256": SHA,
        "receipt": receipt, "adept_receipt": "",
    }
    scope = source.scope
    with store._lock, store._connect() as db:
        db.execute(
            "INSERT INTO acquisition_ledger("
            "acquisition_id,provider_id,credential_id,account_id,item_id,"
            "identity_id,status,artifact_id,metadata_json,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (record_id, scope.provider_id, scope.credential_id,
             scope.account_id, source.edition_id, scope.identity_id, status,
             publications[1].artifact_id, json.dumps(data), 1, 1),
        )
    return record_id


def relink(rig, *, keeper=None):
    store, root, _paths, (first, second, _foreign), _source, lock_path = rig
    with InterProcessLock(lock_path) as lease:
        return relink_completed_delivery_checkpoints(
            store, library_root=root,
            candidate_placement_id=second.placement_id,
            keeper_placement_id=keeper or first.placement_id,
            shared_lock=lease,
        )


def saved(rig, record_id):
    store = rig[0]
    with store._lock, store._connect() as db:
        return db.execute(
            "SELECT status,artifact_id,metadata_json FROM acquisition_ledger "
            "WHERE acquisition_id=?", (record_id,),
        ).fetchone()


def test_complete_receipt_path_relinked_preserving_exact_origin_and_files(rig):
    store, _root, paths, pubs, _source, _lock = rig
    record_id = journal(rig)
    before = rows(store)
    assert before == (1, 3, 3, 3)
    assert relink(rig) == 1
    assert relink(rig) == 0
    status, artifact_id, raw = saved(rig, record_id)
    stage = json.loads(raw)
    assert status == "complete" and artifact_id == pubs[1].artifact_id
    assert stage["path"] == str(paths[0])
    assert stage["receipt"] == "synthetic-completed-delivery"
    assert stage["source"][1] == "seller-b"
    assert stage["sha256"] == SHA
    assert rows(store) == before
    assert all(path.read_bytes() == BYTES for path in paths)
    assert store.placement_get(pubs[1].placement_id)["state"] == "present"


@pytest.mark.parametrize("case", ["cross_profile", "tampered", "unpublished_receipt", "staged"])
def test_conflicting_receipt_or_physical_scope_cannot_relink(rig, case):
    store, _root, paths, pubs, _source, _lock = rig
    record_id = journal(
        rig, status="canonical_receipt_staged" if case == "staged" else "complete",
        receipt="unknown-delivery" if case == "unpublished_receipt"
        else "synthetic-completed-delivery",
    )
    before = saved(rig, record_id)
    if case == "tampered":
        paths[1].write_bytes(BYTES[:-1] + b"X")
    with pytest.raises(ValueError, match="canonical_receipt_relink_"):
        relink(
            rig, keeper=pubs[2].placement_id if case == "cross_profile" else None,
        )
    assert saved(rig, record_id) == before
    assert paths[0].read_bytes() == BYTES


def test_other_path_in_nested_checkpoint_is_not_silently_rewritten(rig):
    record_id = journal(rig)
    store, _root, paths, _pubs, _source, _lock = rig
    with store._lock, store._connect() as db:
        raw = json.loads(db.execute(
            "SELECT metadata_json FROM acquisition_ledger WHERE acquisition_id=?",
            (record_id,),
        ).fetchone()[0])
        raw["unmigrated_path"] = {"path": str(paths[1])}
        db.execute(
            "UPDATE acquisition_ledger SET metadata_json=? WHERE acquisition_id=?",
            (json.dumps(raw), record_id),
        )
    before = saved(rig, record_id)
    with pytest.raises(ValueError, match="canonical_receipt_relink_checkpoint_conflict"):
        relink(rig)
    assert saved(rig, record_id) == before


def test_sql_failure_rolls_back_checkpoint_and_keeps_both_files(rig):
    record_id = journal(rig)
    store, _root, paths, _pubs, _source, _lock = rig
    before = saved(rig, record_id)
    with store._lock, store._connect() as db:
        db.execute(
            "CREATE TRIGGER fail_relink_receipt BEFORE UPDATE OF metadata_json "
            "ON acquisition_ledger BEGIN "
            "SELECT RAISE(ABORT, 'synthetic_receipt_rollback'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="synthetic_receipt_rollback"):
        relink(rig)
    assert saved(rig, record_id) == before
    assert all(path.read_bytes() == BYTES for path in paths)


def test_live_acquisition_lock_required(rig):
    record_id = journal(rig)
    store, root, paths, (first, second, _foreign), _source, lock_path = rig
    with pytest.raises(ValueError, match="canonical_receipt_relink_invalid_request"):
        relink_completed_delivery_checkpoints(
            store, library_root=root,
            candidate_placement_id=second.placement_id,
            keeper_placement_id=first.placement_id,
            shared_lock=InterProcessLock(lock_path),
        )
    assert json.loads(saved(rig, record_id)[2])["path"] == str(paths[1])


def test_adept_receipt_requires_independent_published_origin(rig):
    record_id = journal(rig)
    storage, root, paths, pubs, source, _lock = rig
    with storage._lock, storage._connect() as db:
        payload = json.loads(db.execute(
            "SELECT metadata_json FROM acquisition_ledger WHERE acquisition_id=?",
            (record_id,),
        ).fetchone()[0])
        payload["adept_receipt"] = "synthetic-adept-final"
        db.execute(
            "UPDATE acquisition_ledger SET metadata_json=? WHERE acquisition_id=?",
            (json.dumps(payload), record_id),
        )
    before = saved(rig, record_id)
    with pytest.raises(ValueError, match="canonical_receipt_relink_origin_not_published"):
        relink(rig)
    assert saved(rig, record_id) == before

    # The ADEPT origin must be independently published for this SAME source
    # and Artifact. A PDF here is synthetic; this models provenance only.
    same_source, snapshot = new_source(
        "A", provider="seller-b", credential="card-b", account="buyer-b",
    )
    assert same_source == source
    publish(
        storage, root, paths[1], same_source, snapshot,
        adept_receipt_ref="synthetic-adept-final",
    )
    links_before = rows(storage)
    assert links_before == (1, 3, 3, 4)
    assert relink(rig) == 1
    stage = json.loads(saved(rig, record_id)[2])
    assert stage["path"] == str(paths[0])
    assert stage["adept_receipt"] == "synthetic-adept-final"
    assert rows(storage) == links_before
    assert all(path.read_bytes() == BYTES for path in paths)
