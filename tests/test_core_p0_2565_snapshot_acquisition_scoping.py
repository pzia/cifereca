"""P0 #2565/#2797: irrelevant foreign acquisition history cannot exhaust a scoped snapshot.

Projection takes place on the immutable in-memory SQLite image (main or WAL).
Exact entitlement-ID collisions and exact borrower/source conflicts remain.
No storage migrations, network access, file mutation or untrusted private logs.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from cifereca.core.library_evidence_scope import scope_evidence
from cifereca.core.library_reconciliation import read_library_snapshot
from tests.test_library_reconciliation import fixture_db, filesystem


@pytest.mark.parametrize("wal", [False, True])
def test_thousand_foreign_receipts_cannot_exhaust_alice_row_budget(tmp_path, wal):
    path = tmp_path / "PRIVATE.sqlite3"
    fixture_db(path)
    connection = sqlite3.connect(path)
    try:
        if wal:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA wal_autocheckpoint=0")
        connection.executemany(
            "INSERT INTO entitlements"
            "(entitlement_id,provider_id,credential_id,account_id,item_id,"
            "identity_id,state,entitlement_type,metadata_json)"
            " VALUES(?,?,?,?,?,?,?,?,?)", [
                ("right-alice", "paris", "login", "primary", "edition", "alice",
                 "active", "loan", "{}"),
                ("right-bob", "paris", "login", "paired", "edition", "bob",
                 "active", "loan", "{}"),
            ],
        )
        base = ("acquisition_id", "entitlement_id", "provider_id", "credential_id",
                "account_id", "item_id", "identity_id", "status", "artifact_id")
        records = [
            ("OWN", "right-alice", "paris", "login", "primary", "edition",
             "alice", "complete", "artifact-a"),
            ("EXACT_OWNER_CONFLICT", "other-right", "paris", "login",
             "primary", "edition", "bob", "complete", "artifact-b"),
            ("EXACT_RIGHT_CONFLICT", "right-alice", "paris", "different",
             "paired", "edition", "bob", "complete", "artifact-c"),
        ]
        records.extend(
            (f"PRIVATE_FOREIGN_{i}", f"foreign-{i}", "paris", "login",
             "paired", "edition", "bob", "complete", "artifact-foreign")
            for i in range(850)
        )
        connection.executemany(
            "INSERT INTO acquisition_ledger (" + ",".join(base) + ") "
            "VALUES (" + ",".join(["?"] * len(base)) + ")", records,
        )
        connection.commit()
        before = filesystem(tmp_path)
        scoped = read_library_snapshot(path, max_rows=128, identity_id="alice")
        assert not scoped.reasons
        assert {r["entitlement_id"] for r in scoped.tables["entitlements"]} == {
            "right-alice",
        }
        expected = {"OWN", "EXACT_OWNER_CONFLICT", "EXACT_RIGHT_CONFLICT"}
        assert {r["acquisition_id"] for r in scoped.tables["acquisition_ledger"]} == expected
        assert "PRIVATE_FOREIGN_" not in json.dumps(scoped.tables)
        assert scope_evidence(scoped, ("alice",)).tables["acquisition_ledger"] == (
            scoped.tables["acquisition_ledger"]
        )
        assert read_library_snapshot(path, max_rows=128).reasons == {
            "snapshot_row_budget",
        }
        assert filesystem(tmp_path) == before
    finally:
        connection.close()


@pytest.mark.parametrize("bad", [None, "", "   "])
def test_legacy_blank_selected_borrower_cannot_join_other_acquisition(bad, tmp_path):
    path = tmp_path / "PRIVATE.sqlite3"
    fixture_db(path)
    with sqlite3.connect(path) as db:
        db.execute(
            "INSERT INTO entitlements"
            "(entitlement_id,provider_id,credential_id,account_id,item_id,"
            "identity_id,state,entitlement_type,metadata_json)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            ("right-a", "numilog", "cred", bad, "edition", "alice",
             "active", "owned", "{}"),
        )
        db.executemany(
            "INSERT INTO acquisition_ledger"
            "(acquisition_id,entitlement_id,provider_id,credential_id,"
            "account_id,item_id,identity_id,status) VALUES(?,?,?,?,?,?,?,?)", [
                ("PRIVATE_OTHER", "other", "numilog", "cred", bad, "edition",
                 "bob", "complete"),
                ("EXACT_RIGHT", "right-a", "numilog", "cred", "borrower",
                 "edition", "bob", "complete"),
            ],
        )
    snapshot = read_library_snapshot(path, max_rows=128, identity_id="alice")
    assert not snapshot.reasons
    assert [r["acquisition_id"] for r in snapshot.tables["acquisition_ledger"]] == [
        "EXACT_RIGHT",
    ]
    assert "PRIVATE_OTHER" not in json.dumps(snapshot.tables)
