"""P0 #5975: real SQLite v4->v17 historical cardinality reconstruction.

The old one-time Kobo repair remains live for schema<5. Exercise its actual
data migrations before converting it to a provider-neutral historical cohort.
No provider/network/filesystem I/O, and no new right/consent/proof.
"""
from __future__ import annotations

import hashlib
import sqlite3

import pytest

from cifereca.storage import Storage


def _old_v4_database(path):
    built = Storage(path)
    built.close()
    suffix = hashlib.sha256(b"vault-b").hexdigest()[:8]
    colliding_identity = "family-kobo-" + suffix
    with sqlite3.connect(path) as db:
        # A genuine v4 lacks the future v15 generic triggers/policy and the
        # v5 supplier indexes. Remove only modern schema guards from our
        # synthetic fixture, not a user's database.
        for trigger in (
            "trg_provider_connection_limit_insert",
            "trg_provider_connection_limit_update",
            "trg_provider_account_limit_insert",
            "trg_provider_account_limit_update",
        ):
            db.execute("DROP TRIGGER IF EXISTS " + trigger)
        db.execute("DROP TABLE IF EXISTS provider_cardinality_policies")
        for idx in (
            "uq_kobo_connection_identity", "uq_kobo_account_identity",
            "uq_ade_profile_identity",
        ):
            db.execute("DROP INDEX IF EXISTS " + idx)
        db.execute("UPDATE metadata SET value='4' WHERE key='schema_version'")
        for identity, label, created in (
            ("family", "Family", 1),
            (colliding_identity, "Unrelated existing identity", 2),
        ):
            db.execute(
                "INSERT INTO identities(identity_id,label,directory_name,"
                "prefer_original_language,reading_languages_json,metadata_json,"
                "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                (identity, label, identity, 0, '["fr"]', '{}', created, created),
            )
        for connection, provider, credential, created in (
            ("con-a", "kobo", "vault-a", 10),
            ("con-b", "kobo", "vault-b", 20),
            ("con-paris", "paris", "library-card", 5),
        ):
            db.execute(
                "INSERT INTO provider_connections(connection_id,identity_id,"
                "provider_id,credential_id,label,enabled,metadata_json,"
                "created_at,updated_at) VALUES(?,?,?,?,?,1,'{}',?,?)",
                (connection, "family", provider, credential, credential, created, created),
            )
        for acc, connection, provider, credential, account, primary, created in (
            ("acct-a", "con-a", "kobo", "vault-a", "buyer-a", 1, 10),
            ("acct-a-extra", "con-a", "kobo", "vault-a", "buyer-a-shadow", 0, 11),
            ("acct-b", "con-b", "kobo", "vault-b", "buyer-b", 1, 20),
            ("acct-paris", "con-paris", "paris", "library-card", "reader", 1, 5),
        ):
            db.execute(
                "INSERT INTO provider_accounts(provider_account_id,connection_id,"
                "identity_id,provider_id,credential_id,account_id,label,is_primary,"
                "metadata_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (acc, connection, "family", provider, credential,
                 account, account, primary, '{}', created, created),
            )
        for credential, account, sha in (
            ("vault-a", "buyer-a", "a" * 64),
            ("vault-b", "buyer-b", "b" * 64),
        ):
            db.execute(
                "INSERT INTO backup_items(provider_id,credential_id,item_id,"
                "title,status,sha256,local_path,updated_at,identity_id,account_id)"
                " VALUES('kobo',?,'edition',?,'complete',?,?,1,'family',?)",
                (credential, credential, sha, "/offline/" + credential + ".epub", account),
            )
            db.execute(
                "INSERT INTO entitlements(entitlement_id,provider_id,credential_id,"
                "account_id,item_id,entitlement_type,state,metadata_json,"
                "discovered_at,updated_at,identity_id)"
                " VALUES(?,'kobo',? ,?,'edition','owned','active','{}',1,1,'family')",
                ("ent-" + credential, credential, account),
            )
            db.execute(
                "INSERT INTO acquisition_ledger(acquisition_id,entitlement_id,"
                "provider_id,credential_id,account_id,item_id,status,metadata_json,"
                "created_at,updated_at,identity_id)"
                " VALUES(?,?,'kobo',?,?,'edition','complete','{}',1,1,'family')",
                ("acq-" + credential, "ent-" + credential, credential, account),
            )
        for kind, scope, profile, created in (
            ("identity", "family", "ade-family", 3),
            ("identity", colliding_identity, "ade-family", 9),
            ("account", "legacy-account", "ade-obsolete", 1),
        ):
            db.execute(
                "INSERT INTO adept_profile_bindings(scope_type,scope_id,"
                "profile_id,created_at,updated_at) VALUES(?,?,?,?,?)",
                (kind, scope, profile, created, created),
            )
        before = tuple(db.execute(
            "SELECT provider_id,credential_id,item_id,title,status,sha256,"
            "local_path FROM backup_items ORDER BY credential_id"
        ).fetchall())
    return colliding_identity, before


def test_v4_historical_kobo_scope_repair_preserves_archives_and_rights(tmp_path):
    path = tmp_path / "historical-v4.sqlite"
    colliding, before = _old_v4_database(path)
    expected = colliding + "-2"
    store = Storage(path)
    try:
        assert store.schema_version() == 18
        con = {r["credential_id"]: r["identity_id"]
               for r in store.provider_connections()}
        assert con == {
            "vault-a": "family",
            "vault-b": expected,
            "library-card": "family",
        }
        accounts = {r["credential_id"]: r for r in store.provider_accounts()}
        assert set(accounts) == {"vault-a", "vault-b", "library-card"}
        assert accounts["vault-b"]["identity_id"] == expected
        assert accounts["vault-a"]["account_id"] == "buyer-a"
        assert accounts["library-card"]["identity_id"] == "family"

        with store._lock, store._connect() as db:
            assert tuple(db.execute(
                "SELECT provider_id,credential_id,item_id,title,status,sha256,"
                "local_path FROM backup_items ORDER BY credential_id"
            ).fetchall()) == before
            for table in ("backup_items", "entitlements", "acquisition_ledger"):
                rows = db.execute(
                    "SELECT credential_id,identity_id FROM " + table
                    + " WHERE provider_id='kobo' ORDER BY credential_id"
                ).fetchall()
                assert rows == [("vault-a", "family"), ("vault-b", expected)]
            profiles = db.execute(
                "SELECT scope_type,scope_id,profile_id FROM adept_profile_bindings"
            ).fetchall()
            assert profiles == [("identity", "family", "ade-family")]
            assert db.execute(
                "SELECT connection_limit,account_limit FROM "
                "provider_cardinality_policies WHERE provider_id='kobo'"
            ).fetchone() == (1, 1)
            assert db.execute(
                "SELECT COUNT(*) FROM source_references"
            ).fetchone()[0] == 0
            assert db.execute(
                "SELECT COUNT(*) FROM origin_links"
            ).fetchone()[0] == 0
            with pytest.raises(sqlite3.IntegrityError):
                db.execute(
                    "INSERT INTO provider_connections(connection_id,identity_id,"
                    "provider_id,credential_id,label,enabled,metadata_json,"
                    "created_at,updated_at)"
                    " VALUES('illegal','family','kobo','vault-c','C',1,'{}',50,50)"
                )
    finally:
        store.close()

    reopened = Storage(path)
    try:
        assert reopened.schema_version() == 18
        con = {r["credential_id"]: r["identity_id"]
               for r in reopened.provider_connections()}
        assert con["vault-b"] == expected
        assert len(reopened.provider_accounts()) == 3
        assert reopened.backup_get("kobo", "vault-b", "edition")["sha256"] == "b" * 64
    finally:
        reopened.close()



def test_v5_repair_algorithm_handles_frozen_synthetic_historical_cohort(
    tmp_path, monkeypatch,
):
    """The migration algorithm is generic; the deployed historic cohort is not."""
    import cifereca.storage as module

    assert module._LEGACY_V5_SINGLE_CONNECTION_PROVIDERS == ("kobo",)
    # Test-only alternate historical cohort; not a live provider descriptor,
    # not a policy change, and not authority to mint canonical rights.
    monkeypatch.setattr(
        module, "_LEGACY_V5_SINGLE_CONNECTION_PROVIDERS",
        ("kobo", "synthetic-provider"),
    )
    path = tmp_path / "historic-synthetic.sqlite"
    created = Storage(path)
    created.close()
    with sqlite3.connect(path) as db:
        for trigger in (
            "trg_provider_connection_limit_insert",
            "trg_provider_connection_limit_update",
            "trg_provider_account_limit_insert",
            "trg_provider_account_limit_update",
        ):
            db.execute("DROP TRIGGER IF EXISTS " + trigger)
        db.execute("DROP TABLE IF EXISTS provider_cardinality_policies")
        db.execute("DROP INDEX IF EXISTS uq_ade_profile_identity")
        db.execute("UPDATE metadata SET value='4' WHERE key='schema_version'")
        db.execute(
            "INSERT INTO identities(identity_id,label,directory_name,"
            "prefer_original_language,reading_languages_json,metadata_json,"
            "created_at,updated_at) VALUES('alice','Alice','alice',0,'[]','{}',1,1)"
        )
        for credential, created_at in (("card-a", 10), ("card-b", 20)):
            db.execute(
                "INSERT INTO provider_connections(connection_id,identity_id,"
                "provider_id,credential_id,label,enabled,metadata_json,"
                "created_at,updated_at)"
                " VALUES(?, 'alice', 'synthetic-provider',?, ?,1,'{}',?,?)",
                ("con-" + credential, credential, credential,
                 created_at, created_at),
            )
            db.execute(
                "INSERT INTO provider_accounts(provider_account_id,connection_id,"
                "identity_id,provider_id,credential_id,account_id,label,is_primary,"
                "metadata_json,created_at,updated_at)"
                " VALUES(?,?,'alice','synthetic-provider',?,?,'Reader',1,'{}',?,?)",
                ("acc-" + credential, "con-" + credential, credential,
                 "account-" + credential, created_at, created_at),
            )
            db.execute(
                "INSERT INTO backup_items(provider_id,credential_id,item_id,"
                "title,status,identity_id,account_id,updated_at)"
                " VALUES('synthetic-provider',?,'edition','Historical','complete',"
                "'alice',?,?)",
                (credential, "account-" + credential, created_at),
            )
    migrated = Storage(path)
    try:
        assert migrated.schema_version() == 18
        derived = "alice-synthetic-provider-" + hashlib.sha256(b"card-b").hexdigest()[:8]
        con = {r["credential_id"]: r["identity_id"]
               for r in migrated.provider_connections()}
        assert con == {"card-a": "alice", "card-b": derived}
        accounts = {r["credential_id"]: r["identity_id"]
                    for r in migrated.provider_accounts()}
        assert accounts == con
        archive = {r["credential_id"]: r["identity_id"]
                   for r in migrated.backup_list("synthetic-provider")}
        assert archive == con
        assert migrated.entitlements() == []
        assert migrated.acquisitions() == []
        with migrated._lock, migrated._connect() as db:
            assert db.execute(
                "SELECT connection_limit FROM provider_cardinality_policies"
                " WHERE provider_id='synthetic-provider'"
            ).fetchone() is None
    finally:
        migrated.close()
