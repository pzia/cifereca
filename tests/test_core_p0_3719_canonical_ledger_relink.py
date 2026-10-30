"""#3719/#3185: canonical completed-delivery path rebind is SQL-only.

All data and files are synthetic; neither acquisition nor DRM/network I/O.
"""
import json
import sqlite3
import time

import pytest

from cifereca.core.canonical_dedup_ledger_relink import (
    relink_canonical_completed_delivery_paths,
)
from cifereca.core.canonical_recovery import (
    _scope, _stage_id, recover_completed_delivery,
)
from cifereca.locking import InterProcessLock
from tests.test_core_p0_3171_atomic_publication_v14 import (
    BYTES, SHA, new_source, publish, register, rows, store, target,
)


def _receipt(storage, source, placement, path, *, offer="offer-1", status="complete"):
    reference = _stage_id(source, offer)
    metadata = {
        "schema": "canonical.completed-delivery.v1",
        "source": _scope(source), "offer_id": offer,
        "path": str(path), "sha256": SHA,
        "receipt": "synthetic-completed-delivery", "adept_receipt": "",
    }
    with storage._lock, storage._connect() as db:
        db.execute(
            "INSERT INTO acquisition_ledger(acquisition_id,provider_id,"
            "credential_id,account_id,item_id,identity_id,status,artifact_id,"
            "metadata_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (reference, source.scope.provider_id, source.scope.credential_id,
             source.scope.account_id, source.edition_id, source.scope.identity_id,
             status, placement.artifact_id if status == "complete" else None,
             json.dumps(metadata), int(time.time()), int(time.time())),
        )
    return reference


def _path(storage, reference):
    with storage._lock, storage._connect() as db:
        raw = db.execute(
            "SELECT metadata_json FROM acquisition_ledger WHERE acquisition_id=?",
            (reference,),
        ).fetchone()[0]
    return json.loads(raw)["path"]


@pytest.fixture
def rig(store, tmp_path, monkeypatch):
    root = tmp_path / "EBooks"
    first, a = new_source(
        "A", provider="seller-a", credential="card-a", account="borrower-a",
    )
    second, b = new_source(
        "A", provider="seller-b", credential="card-b", account="borrower-b",
    )
    third, c = new_source(
        "B", provider="seller-c", credential="card-c", account="borrower-c",
    )
    for profile, provider, credential, account in (
        ("A", "seller-a", "card-a", "borrower-a"),
        ("A", "seller-b", "card-b", "borrower-b"),
        ("B", "seller-c", "card-c", "borrower-c"),
    ):
        register(store, profile, provider, credential, account=account)
    paths = [
        target(tmp_path, "A", "first.pdf"),
        target(tmp_path, "A", "second.pdf"),
        target(tmp_path, "B", "third.pdf"),
    ]
    publications = [
        publish(store, root, paths[0], first, a),
        publish(store, root, paths[1], second, b),
        publish(store, root, paths[2], third, c),
    ]
    refs = [
        _receipt(store, first, publications[0], paths[0]),
        _receipt(store, second, publications[1], paths[1]),
        _receipt(store, third, publications[2], paths[2]),
    ]
    lock_path = tmp_path / "locks" / "acquisition.lock"
    monkeypatch.setattr(
        "cifereca.core.canonical_recovery.acquisition_lock_file",
        lambda: lock_path,
    )
    return store, root, (first, second, third), paths, publications, refs, lock_path


def _relink(rig, *, candidate=None, keeper=None):
    storage, root, _sources, _paths, publications, _refs, lock_path = rig
    with InterProcessLock(lock_path) as lock:
        return relink_canonical_completed_delivery_paths(
            storage, library_root=root,
            candidate_placement_id=candidate or publications[1].placement_id,
            keeper_placement_id=keeper or publications[0].placement_id,
            shared_lock=lock,
        )


def test_relink_preserves_other_sources_and_recovery_without_delete(rig):
    storage, root, sources, paths, publications, refs, lock_path = rig
    initial = rows(storage)
    assert initial == (1, 3, 3, 3)
    assert _relink(rig) == 1
    assert _relink(rig) == 0
    assert [_path(storage, ref) for ref in refs] == [
        str(paths[0]), str(paths[0]), str(paths[2]),
    ]
    with InterProcessLock(lock_path) as lock:
        recovered = recover_completed_delivery(
            storage, source=sources[1], offer_id="offer-1",
            library_root=root, shared_lock=lock,
        )
    assert recovered is not None and recovered.path == paths[0]
    assert recovered.final_sha256 == SHA
    assert rows(storage) == initial
    assert storage.placement_get(publications[1].placement_id)["state"] == "present"
    assert all(path.read_bytes() == BYTES for path in paths)


def test_cross_profile_and_changed_file_never_relink(rig):
    storage, _root, _sources, paths, publications, refs, _lock_path = rig
    with pytest.raises(ValueError, match="scope_changed"):
        _relink(rig, keeper=publications[2].placement_id)
    paths[0].write_bytes(BYTES[:-1] + b"X")
    with pytest.raises(ValueError, match="physical_evidence_changed"):
        _relink(rig)
    assert _path(storage, refs[1]) == str(paths[1])
    assert all(path.is_file() for path in paths)


def test_staged_receipt_blocks_rebinding_even_with_valid_origin(rig):
    storage, _root, sources, paths, publications, refs, _lock = rig
    staged = _receipt(
        storage, sources[1], publications[1], paths[1],
        offer="pending-recovery", status="canonical_receipt_staged",
    )
    with pytest.raises(ValueError, match="dependency_ambiguous"):
        _relink(rig)
    assert _path(storage, refs[1]) == _path(storage, staged) == str(paths[1])


def test_unknown_ledger_metadata_path_blocks_rebinding(rig):
    storage, _root, sources, paths, publications, refs, _lock = rig
    unknown = _receipt(
        storage, sources[1], publications[1], paths[1], offer="old",
    )
    with storage._lock, storage._connect() as db:
        db.execute(
            "UPDATE acquisition_ledger SET metadata_json=? WHERE acquisition_id=?",
            (json.dumps({"legacy": {"retained_path": str(paths[1])}}), unknown),
        )
    with pytest.raises(ValueError, match="dependency_ambiguous"):
        _relink(rig)
    assert _path(storage, refs[1]) == str(paths[1])


def test_origin_not_matching_receipt_cannot_authorize_recovery_rebind(rig):
    storage, _root, _sources, paths, _publications, refs, _lock = rig
    with storage._lock, storage._connect() as db:
        db.execute(
            "UPDATE acquisition_ledger SET metadata_json=json_set("
            "metadata_json, '$.receipt', 'forged-receipt') WHERE acquisition_id=?",
            (refs[1],),
        )
    with pytest.raises(ValueError, match="dependency_ambiguous"):
        _relink(rig)
    assert _path(storage, refs[1]) == str(paths[1])


def test_sql_failure_rolls_back_all_path_changes(rig):
    storage, _root, sources, paths, publications, refs, _lock = rig
    second = _receipt(
        storage, sources[1], publications[1], paths[1], offer="offer-2",
    )
    with storage._lock, storage._connect() as db:
        db.execute(
            "CREATE TRIGGER fail_second BEFORE UPDATE OF metadata_json "
            "ON acquisition_ledger WHEN NEW.acquisition_id=? "
            "BEGIN SELECT RAISE(ABORT,'synthetic_ledger_fault'); END".replace(
                "NEW.acquisition_id=?", "NEW.acquisition_id='" + second + "'"
            )
        )
    with pytest.raises(sqlite3.IntegrityError, match="synthetic_ledger_fault"):
        _relink(rig)
    assert _path(storage, refs[1]) == _path(storage, second) == str(paths[1])
    assert all(path.read_bytes() == BYTES for path in paths)


def test_requires_live_acquisition_lock(rig):
    storage, root, _sources, paths, publications, refs, lock_path = rig
    with pytest.raises(ValueError, match="invalid_request"):
        relink_canonical_completed_delivery_paths(
            storage, library_root=root,
            candidate_placement_id=publications[1].placement_id,
            keeper_placement_id=publications[0].placement_id,
            shared_lock=InterProcessLock(lock_path),
        )
    assert _path(storage, refs[1]) == str(paths[1])
