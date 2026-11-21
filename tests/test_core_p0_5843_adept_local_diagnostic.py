"""Local ADEPT diagnostic never starts Core or creates a database."""
import json
import sqlite3
import sys

import pytest

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
        store.close()
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
        store.close()
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


def test_exact_loan_origin_join_and_redacted_divergences(tmp_path):
    path = tmp_path / "evidence.sqlite"
    store = seed(path)
    try:
        with store._lock, store._connect() as db:
            for ref, credential, state in (
                ("PRIVATE_REF_1", "PRIVATE_C", "active"),
                ("PRIVATE_REF_2", "PRIVATE_C", "inactive"),
                ("PRIVATE_REF_3", "PRIVATE_D", "unknown"),
            ):
                db.execute(
                    "INSERT INTO source_references(source_reference_id,identity_id,provider_id,"
                    "credential_id,account_id,branch_id,edition_id,right_id,reference_kind,"
                    "state,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,1,1)",
                    (ref, "PRIVATE_A", "library1", credential, "PRIVATE_ACCOUNT",
                     "branch", ref, ref, "loan", state))
            for index, ref, owner, receipt in (
                (1, "PRIVATE_REF_1", "PRIVATE_A", "PRIVATE_RECEIPT"),
                (2, "PRIVATE_REF_1", "PRIVATE_B", ""),
                (3, "PRIVATE_REF_3", "PRIVATE_A", "PRIVATE_OTHER_RECEIPT"),
            ):
                artifact = "PRIVATE_ARTIFACT_" + str(index)
                db.execute(
                    "INSERT INTO artifacts(artifact_id,sha256,size,media_format,"
                    "created_at,updated_at) VALUES(?,?,1,'epub',1,1)",
                    (artifact, format(index, "064x")))
                db.execute(
                    "INSERT INTO origin_links(origin_link_id,origin_kind,identity_id,"
                    "artifact_id,source_reference_id,evidence_ref,created_at,updated_at)"
                    " VALUES(?,'adept',?,?,?,?,1,1)",
                    ("PRIVATE_ORIGIN_" + str(index), owner, artifact, ref, receipt))
        store.close()
        report = diagnostic.diagnose_local_adept(
            database_path=path, identity="PRIVATE_A", provider="library1",
            credential="PRIVATE_C", account_id="PRIVATE_ACCOUNT")
        assert report["status"] == "complete"
        record, = report["accounts"]
        assert record["loan_references"] == 2
        assert record["adept_origin_records"] == 2
        assert record["origin_owner_mismatch"] == 1
        assert record["origin_receipt_missing"] == 1
        assert record["origin_multiple_records"] == 1
        assert record["loan_references_without_adept_origin"] == 1
        assert record["loan_reference_states"] == {"active": 1, "inactive": 1, "unknown": 0}
        assert record["current_right"] == "unknown"
        assert report["authorization_attested"] is False
        assert "PRIVATE" not in json.dumps(report)
        other, = diagnostic.diagnose_local_adept(
            database_path=path, identity="PRIVATE_A", credential="PRIVATE_D")["accounts"]
        assert other["adept_origin_records"] == 1
        assert other["origin_owner_mismatch"] == 0
        assert other["loan_reference_states"]["unknown"] == 1
    finally:
        store.close()


def test_never_open_source_sqlite_even_when_image_is_unavailable(tmp_path, monkeypatch):
    path = tmp_path / "evidence.sqlite"
    store = seed(path)
    store.close()
    wal = path.with_name(path.name + "-wal")
    shm = path.with_name(path.name + "-shm")
    main_before = path.read_bytes()
    real_connect = sqlite3.connect
    opened = []

    def memory_only(name, *args, **kwargs):
        opened.append(name)
        assert name == ":memory:"
        return real_connect(name, *args, **kwargs)

    monkeypatch.setattr(diagnostic.sqlite3, "connect", memory_only)
    assert diagnostic.diagnose_local_adept(database_path=path)["status"] == "complete"
    assert opened == [":memory:"]
    # A WAL without a coherent SHM must not be opened or ignored.
    wal.write_bytes(b"incomplete private WAL")
    report = diagnostic.diagnose_local_adept(database_path=path)
    assert report["code"] == "snapshot_live_unavailable"
    assert not shm.exists()
    assert opened == [":memory:"]
    assert wal.read_bytes() == b"incomplete private WAL"
    assert path.read_bytes() == main_before
    assert "private WAL" not in json.dumps(report)


@pytest.mark.skipif(sys.platform != "linux", reason="isolated source-WAL reader is Linux-only")
def test_live_wal_is_read_without_writing_source_sidecars(tmp_path):
    pytest.importorskip("apsw")
    path = tmp_path / "evidence.sqlite"
    store = seed(path)
    try:
        paths = [path, path.with_name(path.name + "-wal"), path.with_name(path.name + "-shm")]
        assert all(p.exists() for p in paths)
        before = [p.read_bytes() for p in paths]
        report = diagnostic.diagnose_local_adept(database_path=path)
        assert report["status"] == "complete"
        assert report["counts"]["accounts"] == 3
        assert report["accounts"][0]["borrower_grant"] == "explicit_grant_recorded"
        assert [p.read_bytes() for p in paths] == before
        assert "PRIVATE" not in json.dumps(report)
    finally:
        store.close()
