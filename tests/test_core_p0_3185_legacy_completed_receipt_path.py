"""P0 #3185: a completed exact Core receipt is only advisory path evidence.

All SQL, receipts and PDF bytes are synthetic. No user files or supplier I/O.
"""
import json

import pytest

from cifereca.core.canonical_legacy_scope_review import (
    review_legacy_placement_scope,
)
from tests.test_core_p0_3185_canonical_backup_relink import rig
from tests.test_core_p0_3185_legacy_scope_review import _mark_legacy, _review
from tests.test_core_p0_3719_prepare_duplicate import _journal
from tests.test_core_p0_3171_atomic_publication_v14 import BYTES, rows


def _ledger(storage, record_id):
    with storage._lock, storage._connect() as db:
        return db.execute(
            "SELECT metadata_json FROM acquisition_ledger WHERE acquisition_id=?",
            (record_id,),
        ).fetchone()[0]


def test_one_completed_receipt_proves_exact_path_only_for_diagnostic(rig):
    storage, _root, paths, pubs, _lock = rig
    _mark_legacy(rig)
    record_id = _journal(rig)
    original = _ledger(storage, record_id)
    before = rows(storage)
    outcome = _review(rig)
    assert outcome == _review(rig)
    assert outcome.status == "blocked"
    assert outcome.reason == "completed_receipt_path_review_required"
    assert outcome.completed_path_receipts == 1
    assert outcome.matching_borrower_scopes == 1
    assert outcome.physical_pair_verified_at_review is True
    assert outcome.may_migrate_or_retire is False
    assert str(paths[1]) not in repr(outcome)
    assert "buyer-b" not in repr(outcome)
    assert "card-b" not in repr(outcome)
    assert storage.placement_get(pubs[1].placement_id)["provider_id"] == "seller-b"
    assert _ledger(storage, record_id) == original
    assert rows(storage) == before
    assert all(path.read_bytes() == BYTES for path in paths)


@pytest.mark.parametrize("defect", [
    "incomplete", "receipt_other_path", "receipt_wrong_source",
    "receipt_unknown_proof", "receipt_malformed", "receipt_wrong_sha",
])
def test_unverified_or_incomplete_receipt_never_becomes_path_evidence(rig, defect):
    storage, _root, paths, pubs, _lock = rig
    _mark_legacy(rig)
    record_id = _journal(rig)
    original = _ledger(storage, record_id)
    if defect == "incomplete":
        with storage._lock, storage._connect() as db:
            db.execute(
                "UPDATE acquisition_ledger SET status=? WHERE acquisition_id=?",
                ("canonical_receipt_staged", record_id),
            )
    elif defect == "receipt_other_path":
        payload = json.loads(original)
        payload["path"] = str(paths[0])
        with storage._lock, storage._connect() as db:
            db.execute(
                "UPDATE acquisition_ledger SET metadata_json=? WHERE acquisition_id=?",
                (json.dumps(payload), record_id),
            )
    elif defect == "receipt_wrong_source":
        payload = json.loads(original)
        payload["source"][2] = "wrong-credential"
        with storage._lock, storage._connect() as db:
            db.execute(
                "UPDATE acquisition_ledger SET metadata_json=? WHERE acquisition_id=?",
                (json.dumps(payload), record_id),
            )
    elif defect == "receipt_unknown_proof":
        payload = json.loads(original)
        payload["receipt"] = "unpublished-other-proof"
        with storage._lock, storage._connect() as db:
            db.execute(
                "UPDATE acquisition_ledger SET metadata_json=? WHERE acquisition_id=?",
                (json.dumps(payload), record_id),
            )
    elif defect == "receipt_wrong_sha":
        payload = json.loads(original)
        payload["sha256"] = "f" * 64
        with storage._lock, storage._connect() as db:
            db.execute(
                "UPDATE acquisition_ledger SET metadata_json=? WHERE acquisition_id=?",
                (json.dumps(payload), record_id),
            )
    else:
        with storage._lock, storage._connect() as db:
            db.execute(
                "UPDATE acquisition_ledger SET metadata_json=? WHERE acquisition_id=?",
                ('{"source":1,"source":2}', record_id),
            )
    outcome = _review(rig)
    assert outcome.status == "blocked"
    assert outcome.completed_path_receipts == 0
    assert outcome.may_migrate_or_retire is False
    if defect == "receipt_other_path":
        assert outcome.reason == "legacy_file_source_unbound"
    else:
        assert outcome.reason == "legacy_receipt_unverified"
    assert all(p.exists() for p in paths)
    assert storage.placement_get(pubs[1].placement_id)["state"] == "present"


def test_unrelated_profile_cannot_claim_receipt_even_on_same_sha(rig):
    storage, _root, paths, pubs, _lock = rig
    _mark_legacy(rig, index=2, provider="seller-c", edition="book")
    _journal(rig)
    outcome = _review(rig, candidate=2)
    assert outcome.reason == "scope_mismatch"
    assert outcome.completed_path_receipts == 0
    assert not outcome.physical_pair_verified_at_review
    assert all(p.exists() for p in paths)


def test_changed_physical_bytes_never_use_historical_receipt(rig):
    storage, _root, paths, pubs, _lock = rig
    _mark_legacy(rig)
    _journal(rig)
    paths[1].write_bytes(BYTES[:-1] + b"X")
    outcome = _review(rig)
    assert outcome.reason == "physical_unverified"
    assert outcome.completed_path_receipts == 0
    assert storage.placement_get(pubs[1].placement_id)["state"] == "present"
