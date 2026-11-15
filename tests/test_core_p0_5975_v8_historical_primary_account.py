"""P0 #5975: v7->v8 Paris primary alias is still an active historical writer.

A versioned old-database recovery may merge exact Paris rights and receipts
but must not change unknown-provider state or create new rights / source proofs.
"""
from __future__ import annotations

import inspect
import sqlite3

from cifereca.storage import Storage


def test_v8_primary_borrower_legacy_repair_is_real_scoped_and_one_time(tmp_path):
    path = tmp_path / "historical-v7.sqlite"
    fresh = Storage(path)
    fresh.close()
    with sqlite3.connect(path) as db:
        for entitlement_id, provider, account, updated in (
            ("right-paris-old", "paris", "", 1),
            ("right-paris-new", "paris", "primary", 2),
            ("right-foreign", "future-library", "", 3),
        ):
            db.execute(
                "INSERT INTO entitlements("
                "entitlement_id,provider_id,credential_id,account_id,item_id,"
                "entitlement_type,loan_id,state,metadata_json,discovered_at,"
                "updated_at,identity_id) VALUES (?,?,?,?,?,'loan','loan-1',"
                "'active','{}',?,?,?)",
                (entitlement_id, provider, "credential", account,
                 "edition", updated, updated, "alice"),
            )
        for acquisition_id, right_id, provider, account, status, attempts in (
            ("acq-paris-old", "right-paris-old", "paris", "", "pending", 5),
            ("acq-paris-new", "right-paris-new", "paris", "primary", "complete", 2),
            ("acq-foreign", "right-foreign", "future-library", "", "complete", 9),
        ):
            db.execute(
                "INSERT INTO acquisition_ledger("
                "acquisition_id,entitlement_id,provider_id,credential_id,"
                "account_id,item_id,strategy,status,attempt_count,"
                "metadata_json,created_at,updated_at,identity_id)"
                " VALUES (?,?,?,?,?,'edition','loan_auto',?,?,'{}',1,1,'alice')",
                (acquisition_id, right_id, provider, "credential",
                 account, status, attempts),
            )
        db.execute("UPDATE metadata SET value='7' WHERE key='schema_version'")
    migrated = Storage(path)
    try:
        assert migrated.schema_version() == 17
        with migrated._lock, migrated._connect() as db:
            rights = db.execute(
                "SELECT entitlement_id,provider_id,account_id,identity_id "
                "FROM entitlements ORDER BY provider_id"
            ).fetchall()
            receipt = db.execute(
                "SELECT acquisition_id,entitlement_id,provider_id,account_id,"
                "status,attempt_count,identity_id "
                "FROM acquisition_ledger ORDER BY provider_id"
            ).fetchall()
            assert len(rights) == len(receipt) == 2
            paris_right = next(row for row in rights if row[1] == "paris")
            paris_receipt = next(row for row in receipt if row[2] == "paris")
            canonical = Storage._entitlement_id_for({
                "provider_id": "paris", "credential_id": "credential",
                "account_id": "primary", "item_id": "edition",
                "entitlement_type": "loan", "loan_id": "loan-1",
            })
            assert paris_right == (canonical, "paris", "primary", "alice")
            assert paris_receipt[1:] == (
                canonical, "paris", "primary", "complete", 5, "alice"
            )
            assert paris_receipt[0] == Storage._acquisition_id_for({
                "provider_id": "paris", "credential_id": "credential",
                "account_id": "primary", "item_id": "edition",
                "entitlement_type": "loan", "loan_id": "loan-1",
            })
            assert next(row for row in rights if row[1] == "future-library") == (
                "right-foreign", "future-library", "", "alice"
            )
            assert next(row for row in receipt if row[2] == "future-library") == (
                "acq-foreign", "right-foreign", "future-library",
                "", "complete", 9, "alice"
            )
            assert db.execute("SELECT COUNT(*) FROM source_references").fetchone()[0] == 0
            assert db.execute("SELECT COUNT(*) FROM origin_links").fetchone()[0] == 0
    finally:
        migrated.close()
    # Schema v8+ startup must not rerun historical entitlement/receipt mutations.
    reopened = Storage(path)
    try:
        with reopened._lock, reopened._connect() as db:
            assert db.execute(
                "SELECT COUNT(*) FROM entitlements"
            ).fetchone()[0] == 2
            assert db.execute(
                "SELECT COUNT(*) FROM acquisition_ledger"
            ).fetchone()[0] == 2
    finally:
        reopened.close()


def test_v8_still_has_historical_mutating_sql_not_a_retired_writer():
    from cifereca.storage import Storage
    from pathlib import Path
    import json
    from cifereca.core import library_reconciliation  # noqa: F401
    root = Path(__file__).resolve().parents[1]
    ledger = json.loads((root / "docs/provider-business-coupling-review.json").read_text())
    paths = [row for row in ledger["retained_legacy_migrations"]
             if row["id"] == "legacy-sqlite-v8-paris-primary-account-receipt-repair"]
    assert len(paths) == 1 and paths[0]["classification"] == "legacy_mutating_cutover"
    migrate = inspect.getsource(Storage._migrate)
    segment = migrate.split("if version < 8:", 1)[1].split("if version < 9:", 1)[0]
    assert "WHERE provider_id='paris'" in segment
    assert "UPDATE entitlements" in segment
    assert "DELETE FROM acquisition_ledger" in segment
