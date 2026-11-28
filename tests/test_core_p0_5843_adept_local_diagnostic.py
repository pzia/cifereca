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
    assert cli.main(["adept-diagnostic", "--all"]) == 0
    text = capsys.readouterr().out
    assert "borrower_grant.explicit_grant_recorded: 1" in text
    assert "borrower_grant.grant_not_recorded: 2" in text
    assert "historical_adept_rows_without_reference: 0" in text
    assert "PRIVATE" not in text
    assert path.read_bytes() == before
    with pytest.raises(SystemExit) as exc:
        cli.main(["adept-diagnostic", "--help"])
    assert exc.value.code == 0
    help_text = capsys.readouterr().out
    for option in ("--all", "--identity", "--provider", "--credential", "--account-id", "--json"):
        assert option in help_text
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
        # Coverage can flush its own SQLite database while tracing this call.
        # Only its direct instrumentation connection is outside the oracle.
        if sys._getframe(1).f_globals.get("__name__") == "coverage.sqlitedb":
            return real_connect(name, *args, **kwargs)
        opened.append(name)
        assert name == ":memory:"
        return real_connect(name, *args, **kwargs)

    with monkeypatch.context() as scoped:
        scoped.setattr(diagnostic.sqlite3, "connect", memory_only)
        report = diagnostic.diagnose_local_adept(database_path=path)
    assert report["status"] == "complete"
    assert opened == [":memory:"]
    # A WAL without a coherent SHM must not be opened or ignored.
    wal.write_bytes(b"incomplete private WAL")
    with monkeypatch.context() as scoped:
        scoped.setattr(diagnostic.sqlite3, "connect", memory_only)
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


def test_historical_receipts_and_rights_stay_scoped_and_non_authoritative(tmp_path):
    path = tmp_path / "evidence.sqlite"
    store = seed(path)
    try:
        with store._lock, store._connect() as db:
            for i, branch_name, edition, right, state in (
                (1, "branch", "PRIVATE_ED1", "PRIVATE_LOAN1", "active"),
                (2, "branch", "PRIVATE_ED2", "PRIVATE_LOAN2", "inactive"),
                (3, "branch", "PRIVATE_ED3", "PRIVATE_LOAN3", "unknown"),
                (4, "branch", "PRIVATE_ED4", "PRIVATE_LOAN4", "active"),
                (5, "other", "PRIVATE_ED4", "PRIVATE_LOAN4", "active"),
            ):
                db.execute(
                    "INSERT INTO source_references(source_reference_id,identity_id,provider_id,"
                    "credential_id,account_id,branch_id,edition_id,right_id,reference_kind,"
                    "state,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,1,1)",
                    ("PRIVATE_REF" + str(i), "PRIVATE_A", "library1", "PRIVATE_C",
                     "PRIVATE_ACCOUNT", branch_name, edition, right, "loan", state))
            for i, state in ((1, "active"), (2, "active")):
                db.execute(
                    "INSERT INTO entitlements(entitlement_id,identity_id,provider_id,credential_id,"
                    "account_id,item_id,loan_id,entitlement_type,state,discovered_at,updated_at)"
                    " VALUES(?,?,?,?,?,?,?,'loan',?,1,1)",
                    ("PRIVATE_RIGHT" + str(i), "PRIVATE_A", "library1", "PRIVATE_C",
                     "PRIVATE_ACCOUNT", "PRIVATE_ED" + str(i), "PRIVATE_LOAN" + str(i), state))
            for i, credential, profile, raw in (
                (1, "PRIVATE_C", "PRIVATE_B_profile", None),
                (2, "PRIVATE_C", "PRIVATE_OLD_PROFILE", None),
                (3, "PRIVATE_C", None, '{"provider_loan_id":"a","provider_loan_id":"b"}'),
                (4, "PRIVATE_C", "PRIVATE_B_profile", None),
                (1, "PRIVATE_D", "PRIVATE_OTHER_PROFILE", None),
            ):
                metadata = raw if raw is not None else json.dumps({
                    "provider_loan_id": "PRIVATE_LOAN" + str(i),
                    "adept_binding": {"adept_profile_id": profile,
                                      "adept_loan_id": "PRIVATE_ADOBE_LOAN"},
                    "path": "/PRIVATE_PATH",
                })
                db.execute(
                    "INSERT INTO acquisition_ledger(acquisition_id,identity_id,provider_id,"
                    "credential_id,account_id,item_id,status,strategy,executor,metadata_json,"
                    "created_at,updated_at) VALUES(?,?,?,?,?,?,'complete','loan_auto','kobo-adept',?,1,1)",
                    ("PRIVATE_RECEIPT" + credential + str(i), "PRIVATE_A", "library1",
                     credential, "PRIVATE_ACCOUNT", "PRIVATE_ED" + str(i), metadata))
        store.close()
        all_report = diagnostic.diagnose_local_adept(database_path=path)
        assert all_report["recorded_identity_profile_states"] == {"binding_recorded": 2}
        report = diagnostic.diagnose_local_adept(
            database_path=path, identity="PRIVATE_A", credential="PRIVATE_C")
        record, = report["accounts"]
        assert report["recorded_identity_profile_states"] is None
        assert record["legacy_loan_right_records"] == {
            "same_local_state": 1, "different_local_state": 1,
            "not_recorded": 1, "ambiguous_reference": 2,
        }
        assert record["historical_transaction_records"] == {
            "recorded": 2, "unverified": 1, "ambiguous_reference": 2,
        }
        assert record["historical_grant_profile_comparison"] == {
            "same_profile_reference": 1, "different_profile_reference": 1,
        }
        assert report["authorization_attested"] is False
        assert report["profile_inventory"] == "unknown"
        assert record["current_right"] == "unknown"
        assert "PRIVATE" not in json.dumps(report)
        store.adept_profile_binding_set("identity", "PRIVATE_B", "PRIVATE_ROTATED")
        store.adept_profile_binding_set("identity", "PRIVATE_B", "PRIVATE_B_profile")
        store.close()
        record, = diagnostic.diagnose_local_adept(
            database_path=path, identity="PRIVATE_A", credential="PRIVATE_C")["accounts"]
        assert record["historical_grant_profile_comparison"] == {
            "current_explicit_grant_unverified": 2,
        }
    finally:
        store.close()


def test_legacy_only_evidence_does_not_invent_accounts_or_references(tmp_path):
    path = tmp_path / "evidence.sqlite"
    store = seed(path)
    try:
        with store._lock, store._connect() as db:
            for idx, credential, account, edition, right in (
                (1, "PRIVATE_C", "PRIVATE_ACCOUNT", "PRIVATE_ED", "PRIVATE_LOAN"),
                (2, "PRIVATE_C", "PRIVATE_ACCOUNT", "PRIVATE_BAD_ED", ""),
                (3, "PRIVATE_D", "PRIVATE_ACCOUNT", "PRIVATE_OTHER_ED", "PRIVATE_OTHER_LOAN"),
                (4, "PRIVATE_C", "PRIVATE_MISSING_ACCOUNT", "PRIVATE_ORPHAN_ED", "PRIVATE_ORPHAN_LOAN"),
            ):
                db.execute(
                    "INSERT INTO entitlements(entitlement_id,identity_id,provider_id,credential_id,"
                    "account_id,item_id,loan_id,entitlement_type,state,discovered_at,updated_at)"
                    " VALUES(?,?,?,?,?,?,?,'loan','active',1,1)",
                    (f"PRIVATE_RIGHT_{idx}", "PRIVATE_A", "library1", credential,
                     account, edition, right))
                db.execute(
                    "INSERT INTO acquisition_ledger(acquisition_id,identity_id,provider_id,"
                    "credential_id,account_id,item_id,status,strategy,executor,metadata_json,"
                    "created_at,updated_at) VALUES(?,?,?,?,?,?,'complete','loan_auto','kobo-adept',?,1,1)",
                    (f"PRIVATE_RECEIPT_{idx}", "PRIVATE_A", "library1", credential, account,
                     edition, json.dumps({"provider_loan_id": right, "path": "/PRIVATE_PATH"})))
        store.close()
        report = diagnostic.diagnose_local_adept(
            database_path=path, identity="PRIVATE_A", provider="library1",
            credential="PRIVATE_C")
        assert report["status"] == "complete"
        record, = report["accounts"]
        assert record["loan_references"] == 0
        assert record["legacy_loan_right_rows_without_reference"] == 1
        assert record["legacy_loan_right_rows_unverified_coordinates"] == 1
        assert record["historical_adept_rows_without_reference"] == 1
        assert record["historical_adept_rows_unverified_coordinates"] == 1
        assert report["counts"]["legacy_loan_right_rows_without_account"] == 1
        assert report["counts"]["historical_adept_rows_without_account"] == 1
        assert record["current_right"] == "unknown"
        assert report["authorization_attested"] is False
        assert "PRIVATE" not in json.dumps(report)
        absent = diagnostic.diagnose_local_adept(
            database_path=path, identity="PRIVATE_A", provider="library1",
            credential="PRIVATE_C", account_id="PRIVATE_MISSING_ACCOUNT")
        assert absent["accounts"] == []
        assert absent["counts"]["historical_adept_rows_without_account"] == 0
        assert absent["counts"]["legacy_loan_right_rows_without_account"] == 0
        sibling, = diagnostic.diagnose_local_adept(
            database_path=path, credential="PRIVATE_D")["accounts"]
        assert sibling["historical_adept_rows_without_reference"] == 1
        assert sibling["legacy_loan_right_rows_without_reference"] == 1
    finally:
        store.close()


def test_local_canonical_artifact_and_placement_evidence_remains_non_authorizing(tmp_path):
    path = tmp_path / "evidence.sqlite"
    store = seed(path)
    try:
        with store._lock, store._connect() as db:
            for idx, placement_owner, placement_state, canonical in (
                (1, "PRIVATE_A", "present", True),
                (2, "PRIVATE_B", "present", True),
                (3, "PRIVATE_A", "missing", True),
                (4, "PRIVATE_A", "present", False),
            ):
                ref = f"PRIVATE_REF_{idx}"
                digest = format(idx, "064x")
                artifact = "sha256:" + digest if canonical else "PRIVATE_LEGACY_ARTIFACT"
                db.execute(
                    "INSERT INTO source_references(source_reference_id,identity_id,provider_id,"
                    "credential_id,account_id,branch_id,edition_id,right_id,reference_kind,"
                    "state,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,'loan','active',1,1)",
                    (ref, "PRIVATE_A", "library1", "PRIVATE_C", "PRIVATE_ACCOUNT",
                     "branch", ref, ref))
                db.execute("INSERT INTO artifacts(artifact_id,sha256,size,media_format,"
                           "created_at,updated_at) VALUES(?,?,1,'epub',1,1)",
                           (artifact, digest))
                db.execute("INSERT INTO origin_links(origin_link_id,origin_kind,identity_id,"
                           "artifact_id,source_reference_id,evidence_ref,created_at,updated_at)"
                           " VALUES(?,'adept',?,?,?,?,1,1)",
                           (f"PRIVATE_ORIGIN_{idx}", "PRIVATE_A", artifact, ref, "PRIVATE_RECEIPT"))
                db.execute("INSERT INTO placements(placement_id,artifact_id,path,identity_id,"
                           "state,created_at,updated_at) VALUES(?,?,?,?,?,1,1)",
                           (f"PRIVATE_PLACEMENT_{idx}", artifact, f"/PRIVATE_PATH_{idx}",
                            placement_owner, placement_state))
            for idx, owner, ref in (
                (1, "PRIVATE_A", None),
                (2, "PRIVATE_A", "PRIVATE_ABSENT_REF"),
                (3, "PRIVATE_B", None),
            ):
                db.execute("INSERT INTO origin_links(origin_link_id,origin_kind,identity_id,"
                           "artifact_id,source_reference_id,evidence_ref,created_at,updated_at)"
                           " VALUES(?,'adept',?,?,?,?,1,1)",
                           (f"PRIVATE_UNSCOPED_{idx}", owner, "sha256:" + format(1, "064x"),
                            ref, f"PRIVATE_UNSCOPED_RECEIPT_{idx}"))
        store.close()
        selected = diagnostic.diagnose_local_adept(
            database_path=path, identity="PRIVATE_A", credential="PRIVATE_C")
        assert selected["status"] == "complete"
        record, = selected["accounts"]
        assert record["adept_origin_records"] == 4
        assert record["origin_artifact_unverified"] == 1
        assert record["origin_without_same_identity_placement"] == 1
        assert record["origin_without_same_identity_present_record"] == 2
        assert "adept_origins_without_source_reference" not in selected["counts"]
        assert "adept_origins_dangling_source_reference" not in selected["counts"]
        assert selected["physical_bytes_checked"] is False
        assert selected["authorization_attested"] is False
        assert "PRIVATE" not in json.dumps(selected)
        owner = diagnostic.diagnose_local_adept(database_path=path, identity="PRIVATE_A")
        assert owner["counts"]["adept_origins_without_source_reference"] == 1
        assert owner["counts"]["adept_origins_dangling_source_reference"] == 1
        overall = diagnostic.diagnose_local_adept(database_path=path)
        assert overall["counts"]["adept_origins_without_source_reference"] == 2
        assert overall["counts"]["adept_origins_dangling_source_reference"] == 1
    finally:
        store.close()


def test_python_evidence_join_cannot_start_after_deadline():
    # No SQLite query, Core start or partial report is permitted after expiry.
    with pytest.raises(diagnostic._Incomplete, match="snapshot_time_budget"):
        diagnostic._snapshot(None, identity=None, provider=None, credential=None,
                             account_id=None, deadline=0)
