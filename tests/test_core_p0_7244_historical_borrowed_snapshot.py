"""#7244: historical read-only pages must not settle borrowed SQLite writes."""
import sqlite3
from threading import RLock
from types import SimpleNamespace

import pytest

from cifereca.core import adept_historical_maintenance as historical


@pytest.mark.parametrize("method", ["page", "branches"])
def test_historical_read_refuses_borrowed_transaction_without_commit(method):
    db = sqlite3.connect(":memory:")
    try:
        db.execute("CREATE TABLE sentinel(value TEXT)")
        db.execute(
            "CREATE TABLE acquisition_ledger("
            "acquisition_id TEXT, provider_id TEXT, credential_id TEXT,"
            "account_id TEXT, item_id TEXT, status TEXT, metadata_json TEXT,"
            "identity_id TEXT, strategy TEXT, executor TEXT)"
        )
        db.execute(
            "CREATE TABLE source_references("
            "identity_id TEXT, provider_id TEXT, credential_id TEXT,"
            "account_id TEXT, edition_id TEXT, right_id TEXT,"
            "reference_kind TEXT, branch_id TEXT)"
        )
        storage = SimpleNamespace(_lock=RLock(), _connect=lambda: db)

        def read():
            if method == "page":
                return historical._page(storage, "identity", "")
            return historical._branches(
                storage, "identity", "provider", "credential",
                "account", "edition", "loan",
            )

        assert read() == ()
        db.execute("BEGIN IMMEDIATE")
        db.execute("INSERT INTO sentinel(value) VALUES ('uncommitted')")
        with pytest.raises(RuntimeError, match="historical_review_nested_transaction"):
            read()
        assert db.in_transaction
        assert db.execute("SELECT value FROM sentinel").fetchall() == [("uncommitted",)]
        db.rollback()
        assert db.execute("SELECT value FROM sentinel").fetchall() == []
        assert read() == ()
    finally:
        db.close()
