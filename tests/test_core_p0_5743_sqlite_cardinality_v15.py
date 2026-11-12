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
    assert CURRENT_SCHEMA_VERSION == db.schema_version() == 15
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
