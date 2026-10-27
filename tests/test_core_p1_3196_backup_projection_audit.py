"""#3196: fixture-only audit of the v0.7 backup projection against canonical tables."""
from __future__ import annotations

import pytest

from cifereca.core.backup_projection_audit import audit_backup_projection
from cifereca.storage import Storage


def _put(storage, *, identity="alice", item="book", status="pending",
         path=None, sha=None):
    storage.backup_put({
        "provider_id": "kobo", "credential_id": identity + "-credential",
        "identity_id": identity, "account_id": "library",
        "item_id": item, "title": "Synthetic", "format": "epub",
        "status": status, "local_path": str(path) if path else None,
        "sha256": sha, "size": 12 if sha else None,
    })


def test_initial_canonical_mirror_matches_legacy_projection(tmp_path):
    db = Storage(tmp_path / "books.sqlite3")
    try:
        _put(db, path=tmp_path / "book.epub", sha="a" * 64)
        assert audit_backup_projection(db, identity_id="alice") == {
            "status": "complete", "scanned": 1,
            "missing_entitlement": 0, "missing_acquisition": 0,
            "scope_mismatch": 0, "status_divergence": 0,
            "artifact_divergence": 0, "placement_missing": 0,
            "placement_scope_mismatch": 0,
            "canonical_scanned": 1,
            "canonical_without_projection": 0,
            "canonical_projection_owner_conflict": 0,
            "canonical_projection_scope_unverified": 0,
        }
        # The audit does not inspect filesystem presence or grant a right.
        assert not (tmp_path / "book.epub").exists()
    finally:
        db.close()


def test_canonical_progress_is_not_overwritten_by_read_only_audit(tmp_path):
    db = Storage(tmp_path / "books.sqlite3")
    try:
        _put(db, status="pending")
        with db._lock, db._connect() as connection:
            connection.execute(
                "UPDATE acquisition_ledger SET status='complete' "
                "WHERE provider_id='kobo' AND identity_id='alice'"
            )
        before = db.acquisitions(provider_id="kobo")
        found = audit_backup_projection(db, identity_id="alice")
        assert found["status"] == "complete"
        assert found["status_divergence"] == 1
        assert found["missing_entitlement"] == 0
        assert found["missing_acquisition"] == 0
        assert db.acquisitions(provider_id="kobo") == before
        assert db.backup_list("kobo")[0]["status"] == "pending"
    finally:
        db.close()


def test_audit_differentiates_missing_right_receipt_and_wrong_owner(tmp_path):
    db = Storage(tmp_path / "books.sqlite3")
    try:
        _put(db, item="wrong-owner")
        _put(db, item="missing-right")
        _put(db, item="missing-receipt")
        with db._lock, db._connect() as connection:
            connection.execute(
                "UPDATE entitlements SET identity_id='bob' "
                "WHERE provider_id='kobo' AND item_id='wrong-owner'"
            )
            connection.execute(
                "DELETE FROM entitlements WHERE provider_id='kobo' "
                "AND item_id='missing-right'"
            )
            connection.execute(
                "DELETE FROM acquisition_ledger WHERE provider_id='kobo' "
                "AND item_id='missing-receipt'"
            )
        result = audit_backup_projection(db, identity_id="alice")
        assert result["scanned"] == 3
        assert result["status"] == "complete"
        assert result["scope_mismatch"] == 1
        assert result["missing_entitlement"] == 1
        assert result["missing_acquisition"] == 1
    finally:
        db.close()


def test_artifact_and_placement_mismatches_do_not_unlink_or_reassign(tmp_path):
    db = Storage(tmp_path / "books.sqlite3")
    target = tmp_path / "local.epub"
    try:
        target.write_bytes(b"synthetic book")
        _put(db, path=target, sha="a" * 64)
        with db._lock, db._connect() as connection:
            connection.execute(
                "UPDATE acquisition_ledger SET artifact_id=? WHERE provider_id='kobo'",
                ("sha256:" + "b" * 64,),
            )
            connection.execute(
                "UPDATE placements SET identity_id='bob' "
                "WHERE provider_id='kobo'"
            )
        assert audit_backup_projection(db, identity_id="alice")["artifact_divergence"] == 1
        assert audit_backup_projection(db, identity_id="alice")["placement_scope_mismatch"] == 1
        assert target.is_file() and target.read_bytes() == b"synthetic book"
    finally:
        db.close()


def test_missing_sqlite_placement_is_explicit_not_a_missing_local_file(tmp_path):
    db = Storage(tmp_path / "books.sqlite3")
    target = tmp_path / "example.epub"
    try:
        _put(db, path=target)
        with db._lock, db._connect() as connection:
            connection.execute("DELETE FROM placements WHERE provider_id='kobo'")
        result = audit_backup_projection(db, identity_id="alice")
        assert result["placement_missing"] == 1
        assert result["status"] == "complete"
    finally:
        db.close()


def test_budget_and_owner_scope_never_report_partial_counts_as_complete(tmp_path):
    db = Storage(tmp_path / "books.sqlite3")
    try:
        _put(db, identity="alice", item="one")
        _put(db, identity="alice", item="two")
        _put(db, identity="bob", item="third")
        result = audit_backup_projection(db, identity_id="alice", max_rows=1)
        assert result["status"] == "budget_exceeded"
        assert result["scanned"] == 0
        assert result["status_divergence"] == 0
        assert audit_backup_projection(db, identity_id="bob")["scanned"] == 1
        assert audit_backup_projection(db, identity_id="alice")["scanned"] == 2
    finally:
        db.close()


def test_old_compatibility_row_without_identity_never_claims_complete(tmp_path):
    db = Storage(tmp_path / "books.sqlite3")
    try:
        _put(db, identity="alice", item="good")
        _put(db, identity="bob", item="legacy")
        with db._lock, db._connect() as connection:
            connection.execute(
                "UPDATE backup_items SET identity_id=NULL "
                "WHERE provider_id='kobo' AND item_id='legacy'"
            )
        result = audit_backup_projection(db, identity_id="alice")
        assert result["status"] == "scope_unverified"
        assert result["scanned"] == 0
        # Inconsistent legacy metadata stays unchanged; no inferred owner.
        assert next(row for row in db.backup_list("kobo")
                    if row["item_id"] == "legacy")["identity_id"] is None
    finally:
        db.close()


@pytest.mark.parametrize("owner,cap", [
    ("", 10), ("alice", 0), ("alice", 501), ("alice", True),
    ("alice\x00bob", 10), (None, 10),
])
def test_invalid_or_oversized_scope_rejected_without_db_read(tmp_path, owner, cap):
    db = Storage(tmp_path / "books.sqlite3")
    try:
        result = audit_backup_projection(db, identity_id=owner, max_rows=cap)
        assert result["status"] == "invalid_scope"
        assert result["scanned"] == 0
    finally:
        db.close()


def test_reverse_audit_finds_canonical_acquisition_without_legacy_row(tmp_path):
    """A canonical receipt remains authoritative even if no backup row exists."""
    db = Storage(tmp_path / "books.sqlite3")
    try:
        _put(db, identity="alice", item="legacy")
        db.acquisition_put({
            "identity_id": "alice", "provider_id": "kobo",
            "credential_id": "alice-credential", "account_id": "library",
            "item_id": "canonical-only", "title": "Canonical book",
            "status": "complete",
        })
        canonical = db.acquisitions(provider_id="kobo")
        result = audit_backup_projection(db, identity_id="alice")
        assert result["status"] == "complete"
        assert result["scanned"] == 1
        assert result["canonical_scanned"] == 2
        assert result["canonical_without_projection"] == 1
        assert result["canonical_projection_owner_conflict"] == 0
        assert [x["item_id"] for x in db.backup_list("kobo")] == ["legacy"]
        assert db.acquisitions(provider_id="kobo") == canonical
    finally:
        db.close()


def test_reverse_audit_does_not_claim_a_different_borrower_or_unknown_owner(
    tmp_path,
):
    db = Storage(tmp_path / "books.sqlite3")
    try:
        _put(db, identity="alice", item="different-borrower")
        _put(db, identity="alice", item="unknown-owner")
        with db._lock, db._connect() as conn:
            conn.execute(
                "UPDATE acquisition_ledger SET account_id='other-borrower' "
                "WHERE item_id='different-borrower'"
            )
            conn.execute(
                "UPDATE backup_items SET account_id=NULL "
                "WHERE item_id='unknown-owner'"
            )
        result = audit_backup_projection(db, identity_id="alice")
        assert result["status"] == "complete"
        assert result["canonical_scanned"] == 2
        assert result["canonical_without_projection"] == 0
        assert result["canonical_projection_owner_conflict"] == 1
        assert result["canonical_projection_scope_unverified"] == 1
    finally:
        db.close()


def test_reverse_audit_refuses_over_budget_canonical_scope_without_partial_totals(
    tmp_path,
):
    db = Storage(tmp_path / "books.sqlite3")
    try:
        for item in ("one", "two"):
            db.acquisition_put({
                "identity_id": "alice", "provider_id": "kobo",
                "credential_id": "alice-credential", "account_id": "library",
                "item_id": item, "title": "Book", "status": "discovered",
            })
        result = audit_backup_projection(db, identity_id="alice", max_rows=1)
        assert result["status"] == "budget_exceeded"
        assert all(value == 0 for key, value in result.items() if key != "status")
        assert len(db.acquisitions(provider_id="kobo")) == 2
        assert db.backup_list("kobo") == []
    finally:
        db.close()
