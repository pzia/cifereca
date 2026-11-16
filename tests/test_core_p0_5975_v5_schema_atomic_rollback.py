"""#5975: failed historical SQLite v4→v5 account repair must be atomic.

A failed version transition must not partially move another account's
identities, delete ADEPT bindings, or mark an old archive as migrated.
All data is synthetic, local and untrusted, without provider I/O.
"""
from __future__ import annotations

import sqlite3

import pytest

from cifereca.storage import Storage


def _historical_v4(path):
    created = Storage(path)
    created.close()
    with sqlite3.connect(path) as db:
        for name in (
            "trg_provider_connection_limit_insert",
            "trg_provider_connection_limit_update",
            "trg_provider_account_limit_insert",
            "trg_provider_account_limit_update",
        ):
            db.execute("DROP TRIGGER IF EXISTS " + name)
        db.execute("DROP TABLE IF EXISTS provider_cardinality_policies")
        for name in (
            "uq_kobo_connection_identity", "uq_kobo_account_identity",
            "uq_ade_profile_identity",
        ):
            db.execute("DROP INDEX IF EXISTS " + name)
        db.execute("UPDATE metadata SET value='4' WHERE key='schema_version'")
        db.execute(
            "INSERT INTO identities(identity_id,label,directory_name,"
            "prefer_original_language,reading_languages_json,metadata_json,"
            "created_at,updated_at) VALUES('family','Family','family',0,'[]','{}',1,1)"
        )
        for idx, credential in enumerate(("vault-a", "vault-b"), 1):
            db.execute(
                "INSERT INTO provider_connections(connection_id,identity_id,"
                "provider_id,credential_id,label,enabled,metadata_json,"
                "created_at,updated_at) VALUES(?, 'family', 'kobo', ?, ?,1,'{}',?,?)",
                (f"con-{idx}", credential, credential, idx, idx),
            )
            db.execute(
                "INSERT INTO provider_accounts(provider_account_id,connection_id,"
                "identity_id,provider_id,credential_id,account_id,label,is_primary,"
                "metadata_json,created_at,updated_at)"
                " VALUES(?,?,'family','kobo',?,?,'Reader',1,'{}',?,?)",
                (f"acc-{idx}", f"con-{idx}", credential,
                 f"buyer-{idx}", idx, idx),
            )
            db.execute(
                "INSERT INTO backup_items(provider_id,credential_id,item_id,"
                "title,status,updated_at,identity_id,account_id)"
                " VALUES('kobo',?,'edition','Archive','complete',1,'family',?)",
                (credential, f"buyer-{idx}"),
            )
        db.execute(
            "INSERT INTO adept_profile_bindings(scope_type,scope_id,profile_id,"
            "created_at,updated_at)"
            " VALUES('account','old-borrower','ade-account',1,1)"
        )


def _snapshot(db):
    tables = ("identities", "provider_connections", "provider_accounts",
              "adept_profile_bindings", "backup_items", "entitlements",
              "acquisition_ledger", "metadata")
    return {
        table: tuple(db.execute(
            "SELECT * FROM " + table + " ORDER BY 1, 2"
        ).fetchall())
        for table in tables
    }


def test_v5_failure_after_repair_restores_v4_accounts_archives_and_adept(
    tmp_path, monkeypatch,
):
    path = tmp_path / "historic-v4.sqlite"
    _historical_v4(path)
    with sqlite3.connect(path) as sql:
        baseline = _snapshot(sql)
        assert dict(sql.execute(
            "SELECT key,value FROM metadata WHERE key='schema_version'"
        ).fetchall()) == {"schema_version": "4"}

    real_write = Storage._write_schema_version

    def refuse_v5(db, version):
        if version == 5:
            raise RuntimeError("synthetic-v5-checkpoint-failure")
        return real_write(db, version)

    with monkeypatch.context() as scoped:
        scoped.setattr(Storage, "_write_schema_version", staticmethod(refuse_v5))
        with pytest.raises(RuntimeError, match="synthetic-v5-checkpoint-failure"):
            Storage(path)

    with sqlite3.connect(path) as sql:
        assert _snapshot(sql) == baseline
        assert sql.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
            " AND name='uq_kobo_connection_identity'"
        ).fetchone() is None
        assert sql.execute(
            "SELECT value FROM metadata WHERE key='schema_version'"
        ).fetchone() == ("4",)
        assert sql.execute(
            "SELECT COUNT(*) FROM adept_profile_bindings"
            " WHERE scope_type='account'"
        ).fetchone() == (1,)

    # A later normal open must repair once without duplicate profiles or
    # losing any former credential/edition history.
    repaired = Storage(path)
    try:
        assert repaired.schema_version() == 18
        connections = {row["credential_id"]: row["identity_id"]
                       for row in repaired.provider_connections()}
        assert set(connections) == {"vault-a", "vault-b"}
        assert len(set(connections.values())) == 2
        assert len(repaired.backup_list("kobo")) == 2
        assert not repaired.entitlements()
        assert not repaired.acquisitions()
        with repaired._lock, repaired._connect() as sql:
            assert sql.execute(
                "SELECT COUNT(*) FROM adept_profile_bindings"
                " WHERE scope_type='account'"
            ).fetchone() == (0,)
    finally:
        repaired.close()
