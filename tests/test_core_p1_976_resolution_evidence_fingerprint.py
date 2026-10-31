"""#976: snapshot equality is a local read-only preflight, never a licence grant."""
from __future__ import annotations

from copy import deepcopy

import pytest

from cifereca.core.adept_resolution_snapshot import (
    snapshot_paris_adept_local_evidence,
    validate_paris_adept_local_snapshot,
)
from cifereca.storage import Storage
from tests.test_core_p1_976_canonical_originlink_review import _artifact
from tests.test_core_p1_976_legacy_canonical_bridge import (
    EvidenceStore, _canonical, _historical_acq, _historical_right,
)


def _fixture(tmp_path):
    db = Storage(tmp_path / "local-snapshot.db")
    _artifact(db)
    with db._lock, db._connect() as sql:
        _canonical(sql, "source-a", identity="alice", credential="paris-a")
    store = EvidenceStore(
        db, rights=[_historical_right()],
        acquisitions=[_historical_acq()],
    )
    return db, store


def _snapshot(store, loan="paris-loan", item="item-1"):
    return snapshot_paris_adept_local_evidence(
        store, loan_id=loan, item_id=item,
    )


def _validate(store, fingerprint, loan="paris-loan", item="item-1"):
    return validate_paris_adept_local_snapshot(
        store, loan_id=loan, item_id=item,
        expected_fingerprint=fingerprint,
    )


def _no_authority(report):
    assert report["return_authorized"] is False
    assert report["commit_available"] is False
    assert report["adobe_profile_resolution"] == "unknown"
    assert report["requires_explicit_review"] is True


def test_snapshot_has_stable_digest_and_never_exposes_credential_or_receipts(tmp_path):
    db, store = _fixture(tmp_path)
    try:
        rights_before = deepcopy(store.rights)
        ledger_before = deepcopy(store.ledger)
        first = _snapshot(store)
        second = _snapshot(store)
        assert first == second
        assert first["status"] == "local_snapshot"
        assert first["algorithm"] == "sha256:paris-adept-local-evidence-v1"
        assert len(first["fingerprint"]) == 64
        assert first["historical_right_count"] == 1
        assert first["historical_acquisition_count"] == 1
        assert first["canonical_evidence_row_count"] == 2
        assert "SECRET-" not in repr(first)
        assert "adept-lic" not in repr(first)
        _no_authority(first)
        check = _validate(store, first["fingerprint"])
        assert check["status"] == "local_evidence_unchanged_remote_attestation_required"
        assert check["evidence_unchanged"] is True
        assert check["current_fingerprint"] == first["fingerprint"]
        _no_authority(check)
        assert store.rights == rights_before and store.ledger == ledger_before
    finally:
        db.close()


@pytest.mark.parametrize("kind", ["change_receipt", "change_source_state", "add_origin"])
def test_canonical_provenance_change_invalidates_previous_local_snapshot(
    tmp_path, kind,
):
    db, store = _fixture(tmp_path)
    try:
        old = _snapshot(store)["fingerprint"]
        with db._lock, db._connect() as sql:
            if kind == "change_receipt":
                sql.execute(
                    "UPDATE origin_links SET evidence_ref=? WHERE origin_link_id=?",
                    ("DIFFERENT-SECRET-RECEIPT", "source-a-adept"),
                )
            elif kind == "change_source_state":
                sql.execute(
                    "UPDATE source_references SET state='inactive' "
                    "WHERE source_reference_id='source-a'"
                )
            else:
                sql.execute(
                    "INSERT INTO origin_links("
                    "origin_link_id,origin_kind,identity_id,artifact_id,"
                    "source_reference_id,evidence_ref,created_at,updated_at)"
                    " VALUES(?,?,?,?,?,?,?,?)",
                    ("another-adobe-receipt", "adept", "alice",
                     "sha256:" + "a" * 64, "source-a", "DIFFERENT-SECRET", 1, 1),
                )
        result = _validate(store, old)
        assert result["status"] == "local_evidence_changed"
        assert result["evidence_unchanged"] is False
        assert result["current_fingerprint"] != old
        assert "DIFFERENT-SECRET" not in repr(result)
        _no_authority(result)
    finally:
        db.close()


def test_historical_adobe_loan_change_invalidates_snapshot_without_db_mutation(tmp_path):
    db, store = _fixture(tmp_path)
    try:
        old = _snapshot(store)["fingerprint"]
        store.ledger[0]["metadata"]["adept_binding"]["adept_loan_id"] = (
            "NEW-SYNTHETIC-ADOBE-LOAN"
        )
        changed = _validate(store, old)
        assert changed["status"] == "local_evidence_changed"
        assert changed["evidence_unchanged"] is False
        assert "NEW-SYNTHETIC" not in repr(changed)
        _no_authority(changed)
    finally:
        db.close()


def test_order_of_legacy_rows_never_changes_snapshot_digest(tmp_path):
    db, store = _fixture(tmp_path)
    try:
        second_right = _historical_right("bob", "paris-b")
        second_acquisition = _historical_acq("bob", "paris-b")
        store.rights.append(second_right)
        store.ledger.append(second_acquisition)
        before = _snapshot(store)
        store.rights.reverse()
        store.ledger.reverse()
        after = _snapshot(store)
        assert before == after
        assert before["historical_right_count"] == 2
        assert before["historical_acquisition_count"] == 2
        _no_authority(after)
    finally:
        db.close()


def test_unrelated_loan_and_item_are_not_part_of_exact_snapshot(tmp_path):
    db, store = _fixture(tmp_path)
    try:
        old = _snapshot(store)
        store.rights.append(_historical_right("bob", "paris-b"))
        store.rights[-1]["loan_id"] = "different"
        store.ledger.append(_historical_acq("bob", "paris-b"))
        store.ledger[-1]["item_id"] = "different"
        after = _snapshot(store)
        assert after == old
        _no_authority(after)
    finally:
        db.close()


def test_empty_and_missing_local_evidence_cannot_yield_commit_permission(tmp_path):
    db = Storage(tmp_path / "no-evidence.db")
    try:
        store = EvidenceStore(db)
        empty = _snapshot(store)
        assert empty["status"] == "no_local_evidence"
        assert empty["fingerprint"] is None
        _no_authority(empty)
        check = _validate(store, "f" * 64)
        assert check["status"] == "local_evidence_unavailable"
        assert check["evidence_unchanged"] is False
        _no_authority(check)
    finally:
        db.close()


def test_unserializable_historical_receipt_fails_closed_without_leaking_values(tmp_path):
    db, store = _fixture(tmp_path)
    try:
        store.ledger[0]["metadata"]["opaque"] = object()
        result = _snapshot(store)
        assert result["status"] == "unserializable_local_evidence"
        assert result["fingerprint"] is None
        assert "object at" not in repr(result)
        _no_authority(result)
    finally:
        db.close()


@pytest.mark.parametrize(("loan", "item", "proof"), [
    (" ", "item-1", "f" * 64),
    ("paris-loan", "\x00PRIVATE", "f" * 64),
    ("paris-loan", "item-1", "SECRET"),
    ("paris-loan", "item-1", "f" * 63),
    ("paris-loan", "item-1", None),
])
def test_invalid_snapshot_validation_fails_before_any_storage_access(loan, item, proof):
    class Forbidden:
        def __getattribute__(self, name):
            raise AssertionError("invalid request may not touch local storage")

    result = validate_paris_adept_local_snapshot(
        Forbidden(), loan_id=loan, item_id=item, expected_fingerprint=proof,
    )
    assert result["status"] == "invalid_snapshot_request"
    assert result["evidence_unchanged"] is False
    _no_authority(result)
    assert "PRIVATE" not in repr(result)


def test_invalid_snapshot_input_never_opens_storage():
    class Forbidden:
        def __getattribute__(self, name):
            raise AssertionError("invalid scope may not touch SQLite")

    report = snapshot_paris_adept_local_evidence(
        Forbidden(), loan_id={"token": "PRIVATE"}, item_id="item-1",
    )
    assert report["status"] == "invalid_exact_scope"
    assert report["fingerprint"] is None
    _no_authority(report)
    assert "PRIVATE" not in repr(report)
