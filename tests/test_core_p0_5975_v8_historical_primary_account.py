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
        assert migrated.schema_version() == 18
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
                # Historic acquisition_ledger has no loan_id/type columns.
                "entitlement_type": "owned", "loan_id": None,
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
    root = Path(__file__).resolve().parents[1]
    ledger = json.loads((root / "docs/provider-business-coupling-review.json").read_text())
    paths = [row for row in ledger["retained_legacy_migrations"]
             if row["id"] == "legacy-sqlite-v8-paris-primary-account-receipt-repair"]
    assert len(paths) == 1 and paths[0]["classification"] == "legacy_mutating_cutover"
    migrate = inspect.getsource(Storage._migrate)
    segment = migrate.split("if version < 8:", 1)[1].split("if version < 9:", 1)[0]
    assert "WHERE provider_id='paris'" not in segment
    assert "for legacy_provider_id in _LEGACY_V8_PRIMARY_ACCOUNT_PROVIDERS" in segment
    assert "WHERE provider_id=?" in segment
    assert "UPDATE entitlements" in segment
    assert "DELETE FROM acquisition_ledger" in segment


def test_v8_synthetic_historical_cohort_is_not_runtime_policy(tmp_path, monkeypatch):
    """A test-only cohort exercises the historical repair, not source grants."""
    import cifereca.storage as module
    assert module._LEGACY_V8_PRIMARY_ACCOUNT_PROVIDERS == ("paris",)
    monkeypatch.setattr(module, "_LEGACY_V8_PRIMARY_ACCOUNT_PROVIDERS",
                        ("paris", "future-library"))
    path = tmp_path / "synthetic-v7.sqlite"
    created = Storage(path)
    created.close()
    with sqlite3.connect(path) as db:
        for provider in ("future-library", "untouched-source"):
            db.execute(
                "INSERT INTO entitlements("
                "entitlement_id,provider_id,credential_id,account_id,item_id,"
                "entitlement_type,loan_id,state,metadata_json,discovered_at,"
                "updated_at,identity_id) VALUES (?,?,?,'','edition','loan',"
                "'loan-1','active','{}',1,1,'alice')",
                ("right-" + provider, provider, "credential"),
            )
            db.execute(
                "INSERT INTO acquisition_ledger("
                "acquisition_id,entitlement_id,provider_id,credential_id,"
                "account_id,item_id,strategy,status,attempt_count,"
                "metadata_json,created_at,updated_at,identity_id)"
                " VALUES (?,?,?,'credential','','edition','loan_auto',"
                "'complete',3,'{}',1,1,'alice')",
                ("acq-" + provider, "right-" + provider, provider),
            )
        db.execute("UPDATE metadata SET value='7' WHERE key='schema_version'")
    migrated = Storage(path)
    try:
        with migrated._lock, migrated._connect() as db:
            rights = {row[0]: row[1:] for row in db.execute(
                "SELECT provider_id,entitlement_id,account_id "
                "FROM entitlements"
            ).fetchall()}
            receipts = {row[0]: row[1:] for row in db.execute(
                "SELECT provider_id,acquisition_id,account_id "
                "FROM acquisition_ledger"
            ).fetchall()}
            expected_right_id = Storage._entitlement_id_for({
                "provider_id": "future-library", "credential_id": "credential",
                "account_id": "primary", "item_id": "edition",
                "entitlement_type": "loan", "loan_id": "loan-1",
            })
            expected_receipt_id = Storage._acquisition_id_for({
                "provider_id": "future-library", "credential_id": "credential",
                "account_id": "primary", "item_id": "edition",
            })
            assert rights == {
                "future-library": (expected_right_id, "primary"),
                "untouched-source": ("right-untouched-source", ""),
            }
            assert receipts == {
                "future-library": (expected_receipt_id, "primary"),
                "untouched-source": ("acq-untouched-source", ""),
            }
            assert db.execute("SELECT COUNT(*) FROM source_references").fetchone()[0] == 0
            assert db.execute("SELECT COUNT(*) FROM origin_links").fetchone()[0] == 0
            assert db.execute(
                "SELECT COUNT(*) FROM provider_cardinality_policies "
                "WHERE provider_id='future-library'"
            ).fetchone()[0] == 0
    finally:
        migrated.close()


def test_v8_supplier_sql_retirement_does_not_hide_active_historical_writer():
    import json
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    ledger = json.loads((root / "docs/provider-business-coupling-review.json").read_text())
    records = [row for row in ledger["retirements"]
               if row["id"] == "struct-v8-historical-primary-account-supplier-sql-selector-neutralized"]
    assert len(records) == 1
    assert records[0]["original_classification"] == "legacy_mutating_cutover"
    assert records[0]["classification"] == "replaced_supplier_selector_without_retiring_historical_writer"
    assert records[0]["in_ast_metric"] is False
    assert records[0]["pr"] == "#6170"
    assert records[0]["retired_at_sha"] == "31724a9ed64ff2ee8999fddce5e5ed5598cda5e2"
    assert all((root / name).is_file() for name in records[0]["tests"])
    retained = [row for row in ledger["retained_legacy_migrations"]
                if row["id"] == "legacy-sqlite-v8-paris-primary-account-receipt-repair"]
    assert len(retained) == 1
    assert retained[0]["classification"] == "legacy_mutating_cutover"
    assert len(ledger["retained_legacy_migrations"]) == 3


def test_v8_savepoint_rollback_restores_all_old_receipts(tmp_path, monkeypatch):
    """A failed post-dedup schema write rolls back rights and ledgers atomically."""
    import pytest
    path = tmp_path / "v8-rollback.sqlite"
    created = Storage(path)
    created.close()
    with sqlite3.connect(path) as db:
        for serial, account in (("old", ""), ("modern", "primary")):
            db.execute(
                "INSERT INTO entitlements("
                "entitlement_id,provider_id,credential_id,account_id,item_id,"
                "entitlement_type,loan_id,state,metadata_json,discovered_at,"
                "updated_at,identity_id)"
                " VALUES (?,'paris','credential',?,'edition','loan','loan-1',"
                "'active','{}',1,1,'alice')",
                ("right-" + serial, account),
            )
            db.execute(
                "INSERT INTO acquisition_ledger("
                "acquisition_id,entitlement_id,provider_id,credential_id,"
                "account_id,item_id,strategy,status,attempt_count,"
                "metadata_json,created_at,updated_at,identity_id)"
                " VALUES (?,?,'paris','credential',?,'edition','loan_auto',"
                "'complete',1,'{}',1,1,'alice')",
                ("acq-" + serial, "right-" + serial, account),
            )
        db.execute("UPDATE metadata SET value='7' WHERE key='schema_version'")
        before_rights = db.execute(
            "SELECT entitlement_id,account_id FROM entitlements "
            "ORDER BY entitlement_id"
        ).fetchall()
        before_receipts = db.execute(
            "SELECT acquisition_id,entitlement_id,account_id "
            "FROM acquisition_ledger ORDER BY acquisition_id"
        ).fetchall()
    original_write = Storage._write_schema_version

    def fail_after_repair(db, version):
        if version == 8:
            raise RuntimeError("injected_v8_commit_failure")
        return original_write(db, version)

    with monkeypatch.context() as patcher:
        patcher.setattr(Storage, "_write_schema_version",
                        staticmethod(fail_after_repair))
        with sqlite3.connect(path) as db:
            with pytest.raises(RuntimeError, match="injected_v8_commit_failure"):
                Storage._migrate(db)
            assert db.execute(
                "SELECT value FROM metadata WHERE key='schema_version'"
            ).fetchone()[0] == "7"
            assert db.execute(
                "SELECT entitlement_id,account_id FROM entitlements "
                "ORDER BY entitlement_id"
            ).fetchall() == before_rights
            assert db.execute(
                "SELECT acquisition_id,entitlement_id,account_id "
                "FROM acquisition_ledger ORDER BY acquisition_id"
            ).fetchall() == before_receipts

    recovered = Storage(path)
    try:
        assert recovered.schema_version() == 18
        with recovered._lock, recovered._connect() as db:
            assert db.execute("SELECT COUNT(*) FROM entitlements").fetchone()[0] == 1
            assert db.execute("SELECT COUNT(*) FROM acquisition_ledger").fetchone()[0] == 1
    finally:
        recovered.close()
