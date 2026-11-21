"""Local ADEPT diagnostic never starts Core or creates a database."""
import json
import sqlite3

from cifereca import cli
from cifereca.core import adept_local_diagnostic as diagnostic
from cifereca.storage import Storage


def seed(path):
    store = Storage(path)
    with store._lock, store._connect() as db:
        for owner in ("PRIVATE_A", "PRIVATE_B"):
            db.execute("INSERT INTO identities(identity_id,label,created_at,updated_at)"
                       " VALUES(?,?,1,1)", (owner, owner))
            db.execute("INSERT INTO adept_profile_bindings"
                       "(scope_type,scope_id,profile_id,created_at,updated_at)"
                       " VALUES('identity',?,?,1,1)", (owner, owner + "_profile"))
        for i, (owner, provider, credential, account) in enumerate((
            ("PRIVATE_A", "library1", "PRIVATE_C", "PRIVATE_ACCOUNT"),
            ("PRIVATE_A", "library1", "PRIVATE_D", "PRIVATE_ACCOUNT"),
            ("PRIVATE_B", "library2", "PRIVATE_C", "PRIVATE_ACCOUNT"),
        )):
            conn = "connection" + str(i)
            db.execute("INSERT INTO provider_connections"
                       "(connection_id,identity_id,provider_id,credential_id,label,created_at,updated_at)"
                       " VALUES(?,?,?,?,?,1,1)", (conn, owner, provider, credential, owner))
            db.execute("INSERT INTO provider_accounts"
                       "(provider_account_id,connection_id,identity_id,provider_id,credential_id,"
                       "account_id,label,created_at,updated_at) VALUES(?,?,?,?,?,?,?,1,1)",
                       ("account" + str(i), conn, owner, provider, credential, account, owner))
    store.adept_borrower_grant_put(
        provider_id="library1", credential_id="PRIVATE_C", account_id="PRIVATE_ACCOUNT",
        connection_identity_id="PRIVATE_A", borrower_identity_id="PRIVATE_B",
        profile_id="PRIVATE_B_profile", confirmed=True)
    return store


def test_all_filters_revoke_and_aba(tmp_path):
    path = tmp_path / "evidence.sqlite"
    store = seed(path)
    try:
        before = diagnostic.diagnose_local_adept(database_path=path)
        assert before["status"] == "complete"
        assert before["counts"]["accounts"] == 3
        assert before["accounts"][0]["borrower_grant"] == "explicit_grant_recorded"
        assert before["accounts"][1]["borrower_grant"] == "grant_not_recorded"
        assert before["accounts"][2]["borrower_grant"] == "grant_not_recorded"
        assert "PRIVATE" not in json.dumps(before)
        assert before["authorization_attested"] is False
        assert before["profile_inventory"] == "unknown"
        exact = dict(identity="PRIVATE_A", provider="library1",
                     credential="PRIVATE_C", account_id="PRIVATE_ACCOUNT", database_path=path)
        assert diagnostic.diagnose_local_adept(**exact)["counts"]["accounts"] == 1
        store.adept_profile_binding_set("identity", "PRIVATE_B", "PRIVATE_NEW")
        store.adept_profile_binding_set("identity", "PRIVATE_B", "PRIVATE_B_profile")
        revoked = diagnostic.diagnose_local_adept(**exact)
        assert revoked["accounts"][0]["borrower_grant"] == "grant_absent_generation_recorded"
        assert diagnostic.diagnose_local_adept(
            **{**exact, "account_id": "absent"})["counts"]["accounts"] == 0
    finally:
        store.close()


def test_cli_offline_no_core_or_database_writes(tmp_path, monkeypatch, capsys):
    path = tmp_path / "evidence.sqlite"
    store = seed(path)
    store.close()
    before = path.read_bytes()
    monkeypatch.setattr(diagnostic, "database_file", lambda *, create: path)
    def fail(**kwargs):
        raise AssertionError("Core startup forbidden")
    monkeypatch.setattr(cli.CiferecaCore, "open", fail)
    assert cli.main(["adept-diagnostic", "--all", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["counts"]["accounts"] == 3
    assert "PRIVATE" not in json.dumps(result)
    assert path.read_bytes() == before
    assert cli.main(["adept-diagnostic", "--all", "--identity", "PRIVATE_A", "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "invalid_scope"


def test_missing_or_old_db_never_initialized(tmp_path):
    missing = tmp_path / "absent.sqlite"
    assert diagnostic.diagnose_local_adept(database_path=missing)["status"] == "unavailable"
    assert not missing.exists()
    with sqlite3.connect(missing) as db:
        db.execute("CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT)")
        db.execute("INSERT INTO metadata VALUES('schema_version','1')")
    before = missing.read_bytes()
    report = diagnostic.diagnose_local_adept(database_path=missing)
    assert report["code"] == "schema_unsupported"
    assert missing.read_bytes() == before
