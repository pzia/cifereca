"""#5743 — SQLite v15 generic cardinality admission and legacy cutover."""
import sqlite3
from types import SimpleNamespace as NS

import pytest

from cifereca.storage import Storage, CURRENT_SCHEMA_VERSION


def descriptor(name, limit):
    return NS(id=name, state="ready", max_accounts_per_identity=limit)


def connection(provider, credential, identity="alice"):
    return dict(provider_id=provider, credential_id=credential,
                identity_id=identity)


def test_v15_replaces_historic_indexes_only_after_neutral_triggers(tmp_path):
    path = tmp_path / "reader.sqlite3"
    db = Storage(path)
    assert CURRENT_SCHEMA_VERSION == db.schema_version() == 17
    with sqlite3.connect(path) as sql:
        indexes = {r[1] for r in sql.execute("PRAGMA index_list(provider_connections)")}
        assert "uq_kobo_connection_identity" not in indexes
        indexes = {r[1] for r in sql.execute("PRAGMA index_list(provider_accounts)")}
        assert "uq_kobo_account_identity" not in indexes
        row = sql.execute(
            "SELECT connection_limit,account_limit FROM provider_cardinality_policies "
            "WHERE provider_id='kobo'"
        ).fetchone()
        assert row == (1, 1)
        triggers = {r[0] for r in sql.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger'"
        )}
        assert {
            "trg_provider_connection_limit_insert",
            "trg_provider_connection_limit_update",
            "trg_provider_account_limit_insert",
            "trg_provider_account_limit_update",
        } <= triggers
    db.close()


def test_raw_sql_and_idempotent_upsert_honor_legacy_cap(tmp_path):
    path = tmp_path / "reader.sqlite3"
    storage = Storage(path)
    connection_id = storage.provider_connection_put(connection("kobo", "first"))
    storage.provider_connection_put(connection("kobo", "first"))
    with sqlite3.connect(path) as raw:
        with pytest.raises(sqlite3.IntegrityError):
            raw.execute(
                "INSERT INTO provider_connections"
                "(connection_id,identity_id,provider_id,credential_id,label,enabled,"
                "metadata_json,created_at,updated_at) "
                "VALUES('second','alice','kobo','second','x',1,'{}',1,1)"
            )
    storage.provider_account_put({
        "connection_id": connection_id, "identity_id": "alice",
        "provider_id": "kobo", "credential_id": "first", "account_id": "one",
    })
    storage.provider_account_put({
        "connection_id": connection_id, "identity_id": "alice",
        "provider_id": "kobo", "credential_id": "first", "account_id": "one",
    })
    with sqlite3.connect(path) as raw:
        with pytest.raises(sqlite3.IntegrityError):
            raw.execute(
                "INSERT INTO provider_accounts"
                "(provider_account_id,connection_id,identity_id,provider_id,"
                "credential_id,account_id,label,is_primary,metadata_json,"
                "created_at,updated_at) "
                "VALUES('second','" + connection_id +
                "','alice','kobo','first','two','x',0,'{}',1,1)"
            )
    assert len(storage.provider_connections()) == 1
    assert len(storage.provider_accounts()) == 1
    storage.close()


def test_synthetic_policy_is_durable_and_tightening_is_atomic(tmp_path):
    path = tmp_path / "reader.sqlite3"
    storage = Storage(path)
    storage.provider_cardinality_policies_set([descriptor("synthetic", 2)])
    storage.provider_connection_put(connection("synthetic", "first"))
    storage.provider_connection_put(connection("synthetic", "second"))
    with pytest.raises(ValueError, match="provider_cardinality_policy_conflict"):
        storage.provider_cardinality_policies_set([descriptor("synthetic", 1)])
    storage.close()
    reopened = Storage(path)
    with pytest.raises(ValueError):
        reopened.provider_connection_put(connection("synthetic", "third"))
    with sqlite3.connect(path) as sql:
        row = sql.execute(
            "SELECT connection_limit FROM provider_cardinality_policies "
            "WHERE provider_id='synthetic'"
        ).fetchone()
        assert row == (2,)
    reopened.close()


def test_source_account_limit_not_inferred_from_credentials_cap(tmp_path):
    storage = Storage(tmp_path / "reader.sqlite3")
    storage.provider_cardinality_policies_set([descriptor("kobo", 1)])
    with storage._lock, storage._connect() as sql:
        assert sql.execute(
            "SELECT account_limit FROM provider_cardinality_policies "
            "WHERE provider_id='kobo'"
        ).fetchone() == (1,)
    storage.close()


def test_policy_rejects_invalid_cap_without_mutation(tmp_path):
    storage = Storage(tmp_path / "reader.sqlite3")
    with pytest.raises(ValueError, match="invalid_provider_cardinality_descriptor"):
        storage.provider_cardinality_policies_set([descriptor("synthetic", False)])
    with storage._lock, storage._connect() as sql:
        assert sql.execute(
            "SELECT count(*) FROM provider_cardinality_policies "
            "WHERE provider_id='synthetic'"
        ).fetchone() == (0,)
    storage.close()


def _recreate_v14_indexes(path):
    """Synthetic v14 checkout, no user data erased."""
    with sqlite3.connect(path) as sql:
        for trigger in (
            "trg_provider_connection_limit_insert",
            "trg_provider_connection_limit_update",
            "trg_provider_account_limit_insert",
            "trg_provider_account_limit_update",
        ):
            sql.execute("DROP TRIGGER IF EXISTS " + trigger)
        sql.execute("DROP TABLE IF EXISTS provider_cardinality_policies")
        sql.execute(
            "CREATE UNIQUE INDEX uq_kobo_connection_identity "
            "ON provider_connections(identity_id) WHERE provider_id='kobo'"
        )
        sql.execute(
            "CREATE UNIQUE INDEX uq_kobo_account_identity "
            "ON provider_accounts(identity_id) WHERE provider_id='kobo'"
        )
        sql.execute(
            "UPDATE metadata SET value='14' WHERE key='schema_version'"
        )


def test_v14_upgrade_keeps_existing_bindings_and_removes_old_indexes(tmp_path):
    path = tmp_path / "upgrade.sqlite3"
    storage = Storage(path)
    storage.provider_connection_put(connection("kobo", "existing"))
    storage.close()
    _recreate_v14_indexes(path)
    upgraded = Storage(path)
    assert upgraded.schema_version() == 17
    assert len(upgraded.provider_connections()) == 1
    with sqlite3.connect(path) as sql:
        assert sql.execute(
            "SELECT connection_limit FROM provider_cardinality_policies "
            "WHERE provider_id='kobo'"
        ).fetchone() == (1,)
        assert "uq_kobo_connection_identity" not in {
            row[1] for row in sql.execute(
                "PRAGMA index_list('provider_connections')"
            )
        }
    upgraded.close()


def test_v15_migration_failure_rolls_back_indexes_atomically(tmp_path, monkeypatch):
    path = tmp_path / "rollback.sqlite3"
    storage = Storage(path)
    storage.close()
    _recreate_v14_indexes(path)
    original = Storage._write_schema_version

    def reject_v15(db, version):
        if version == 15:
            raise RuntimeError("synthetic_version_write_failure")
        return original(db, version)

    monkeypatch.setattr(Storage, "_write_schema_version", staticmethod(reject_v15))
    with pytest.raises(RuntimeError, match="synthetic_version_write_failure"):
        Storage(path)
    with sqlite3.connect(path) as sql:
        assert sql.execute(
            "SELECT value FROM metadata WHERE key='schema_version'"
        ).fetchone() == ("14",)
        assert "uq_kobo_connection_identity" in {
            row[1] for row in sql.execute(
                "PRAGMA index_list('provider_connections')"
            )
        }
        assert sql.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='provider_cardinality_policies'"
        ).fetchone() is None
    monkeypatch.setattr(Storage, "_write_schema_version", original)
    recovered = Storage(path)
    assert recovered.schema_version() == 17
    recovered.close()
