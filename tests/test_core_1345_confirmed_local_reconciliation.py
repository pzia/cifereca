"""Real SQLite/file/CLI integrity tests for explicit Kobo reconciliation."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from types import SimpleNamespace as NS

import pytest

from cifereca import cli
from cifereca.core import kobo_local_reconciliation as repair
from cifereca.identities import Identity
from cifereca.locking import InterProcessLock
from tests.test_kobo_backup_fault_isolation import _service, _write_epub


@pytest.fixture
def case(tmp_path, monkeypatch):
    service, session = _service(tmp_path, [])
    original = service._kobo_backup_target_dir("default") / "PRIVATE-original.epub"
    _write_epub(original)
    target = original.with_name("PRIVATE-copy.epub")
    target.write_bytes(original.read_bytes())
    sha = hashlib.sha256(target.read_bytes()).hexdigest()
    service.storage.backup_put({
        "provider_id": "kobo", "credential_id": "vault-1", "account_id": "PRIVATE-account",
        "identity_id": "default", "item_id": "PRIVATE-product", "revision_id": "r1",
        "title": "PRIVATE title", "format": "epub", "local_path": str(original),
        "status": "reconcile_required", "size": target.stat().st_size, "sha256": sha,
        "entitlement_type": "owned", "error": "PRIVATE previous error",
    })
    def no_network(*args, **kwargs):
        pytest.fail("Reconciliation must not connect, acquire, open a book or contact DRM")
    for name in ("connect_configured", "sync_kobo_backup", "sync_kobo_backup_account", "_execute_kobo_acquisition"):
        monkeypatch.setattr(service, name, no_network)
    monkeypatch.setattr(session, "backup_manifest", no_network)
    monkeypatch.setattr(repair, "acquisition_lock_file", lambda: tmp_path / "acquisition.lock")
    options = dict(credential_id="vault-1", account_id="PRIVATE-account", item_id="PRIVATE-product",
                   expected_sha256=sha, path=str(target))
    yield NS(service=service, storage=service.storage, original=original, target=target,
             sha=sha, options=options, tmp=tmp_path)
    service.close()


def _dump(case):
    with case.storage._lock, case.storage._connect() as db:
        return {table: db.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
                for table in (*repair._KEYS, "metadata", "entitlements", "provider_connections", "artifacts")}


def _run(case, **kwargs):
    return repair.reconcile_kobo_local(case.service, **{**case.options, **kwargs})


def _apply(case):
    plan = _run(case)
    assert plan["status"] == "preview"
    return _run(case, confirmation=plan["plan_token"])


def test_preview_is_read_only_expurgated_and_confirmation_is_reversible(case):
    before = _dump(case)
    files = {p: p.read_bytes() for p in (case.original, case.target)}
    plan = _run(case)
    assert plan["dry_run"] and plan["requires_explicit_confirmation"]
    assert plan["changed"] == 0 and _dump(case) == before
    public = json.dumps(plan)
    for secret in (case.sha, "PRIVATE", "vault-1", "default", str(case.tmp)):
        assert secret not in public
    done = _run(case, confirmation=plan["plan_token"])
    assert done["status"] == "applied" and done["changed"] == 1
    assert done["file_mutations"] == done["rights_mutations"] == 0
    assert _dump(case)["entitlements"] == before["entitlements"]
    assert _dump(case)["artifacts"] == before["artifacts"]
    ledger = case.storage.backup_get("kobo", "vault-1", "PRIVATE-product")
    assert ledger["local_path"] == str(case.target) and ledger["status"] == "complete"
    placement = case.storage.placement_for_path(case.target)
    assert placement["state"] == "present" and placement["artifact_id"] == "sha256:" + case.sha
    assert len([p for p in case.storage.placements() if p["artifact_id"] == placement["artifact_id"]]) == 2
    assert _run(case, confirmation=plan["plan_token"])["status"] == "already_linked"
    undo_plan = _run(case, path=None, undo_operation=done["operation_id"])
    assert undo_plan["dry_run"] and undo_plan["changed"] == 0
    undone = _run(case, path=None, undo_operation=done["operation_id"], confirmation=undo_plan["plan_token"])
    assert undone["status"] == "undone"
    after = _dump(case)
    for table in before:
        if table != "metadata":
            assert after[table] == before[table], table
    assert _run(case, path=None, undo_operation=done["operation_id"])["status"] == "already_undone"
    assert all(p.read_bytes() == data for p, data in files.items())


@pytest.mark.parametrize("field,value,reason", [
    ("account_id", "other", "source_scope_mismatch"),
    ("identity_id", "other", "source_scope_mismatch"),
    ("expected_sha256", "0" * 64, "artifact_evidence_missing"),
    ("path", "relative.epub", "invalid_path"),
    ("path", "/outside/PRIVATE.epub", "file_outside_identity"),
    ("confirmation", "yes", "invalid_confirmation"),
])
def test_unproven_or_wrong_scope_never_mutates(case, field, value, reason):
    before = _dump(case)
    with pytest.raises(repair.ReconciliationRejected, match="^" + reason + "$"):
        _run(case, **{field: value})
    assert _dump(case) == before


def test_distinct_identity_and_connection_do_not_share_evidence_or_open_files(case, monkeypatch):
    case.service.identities.append(Identity("reader-two", "Other", directory_name="other"))
    case.storage.provider_connection_put({"provider_id": "kobo", "identity_id": "reader-two", "credential_id": "other"})
    def forbidden(*args):
        pytest.fail("No file read for an unproven source")
    monkeypatch.setattr(repair, "_read_hash_within", forbidden)
    before = _dump(case)
    with pytest.raises(repair.ReconciliationRejected):
        _run(case, credential_id="other")
    with pytest.raises(repair.ReconciliationRejected, match="source_scope_mismatch"):
        _run(case, identity_id="reader-two")
    assert _dump(case) == before


@pytest.mark.parametrize("edit", ["right_revoked", "right_revision", "right_account", "missing_sha", "wrong_size", "running"])
def test_active_matching_owned_right_and_prior_hash_are_independent_requirements(case, edit):
    with case.storage._lock, case.storage._connect() as db:
        if edit == "right_revoked":
            db.execute("UPDATE entitlements SET state='expired'")
        elif edit == "right_revision":
            db.execute("UPDATE entitlements SET revision_id='r2'")
        elif edit == "right_account":
            db.execute("UPDATE entitlements SET account_id='other'")
        elif edit == "missing_sha":
            db.execute("UPDATE backup_items SET sha256=NULL")
        elif edit == "wrong_size":
            db.execute("UPDATE backup_items SET size=size+1")
        else:
            db.execute("UPDATE acquisition_ledger SET status='acquiring'")
    before = _dump(case)
    with pytest.raises(repair.ReconciliationRejected):
        _run(case)
    assert _dump(case) == before


def test_different_bytes_are_not_matched_by_title_product_or_filename(case):
    case.target.write_bytes(b"different edition")
    before = _dump(case)
    with pytest.raises(repair.ReconciliationRejected, match="file_hash_mismatch"):
        _run(case)
    assert _dump(case) == before


def test_identity_flat_historical_file_can_be_selected_explicitly(case):
    flat = case.original.parent.parent / "legacy.epub"
    flat.write_bytes(case.target.read_bytes())
    plan = _run(case, path=str(flat))
    assert _run(case, path=str(flat), confirmation=plan["plan_token"])["status"] == "applied"
    assert flat.is_file() and case.original.is_file() and case.target.is_file()


@pytest.mark.parametrize("kind", ["leaf", "ancestor"])
def test_symlinks_are_refused_without_reading_external_bytes(case, kind):
    if kind == "leaf":
        case.target.unlink()
        case.target.symlink_to(case.original)
        path = str(case.target)
    else:
        link = case.original.parent / "linked"
        link.symlink_to(case.original.parent, target_is_directory=True)
        path = str(link / case.target.name)
    before = _dump(case)
    with pytest.raises(repair.ReconciliationRejected, match="file_unverified"):
        _run(case, path=path)
    assert _dump(case) == before and case.original.is_file()


def test_changed_preview_and_database_edit_are_not_overwritten(case):
    plan = _run(case)
    with case.storage._lock, case.storage._connect() as db:
        db.execute("UPDATE backup_items SET error='new observation'")
    before = _dump(case)
    with pytest.raises(repair.ReconciliationRejected, match="preview_changed"):
        _run(case, confirmation=plan["plan_token"])
    assert _dump(case) == before


def test_existing_local_placement_keeps_annotations_and_undo_restores_exact_rows(case):
    placement = case.storage.placement_put({
        "placement_id": "existing-local", "path": str(case.target), "identity_id": "default",
        "provider_id": "local", "source_item_id": "local-id", "state": "unverified",
        "metadata": {"note": "retain this placement"},
    })
    case.storage.catalog_entry_put({
        "catalog_entry_id": "existing-catalogue", "placement_id": placement,
        "identity_id": "default", "provider_id": "local", "item_id": "local-id",
        "title": "Personal title", "media_format": "epub", "local_state": "unverified",
        "metadata": {"path": str(case.target), "note": "retain this annotation"},
    })
    before = _dump(case)
    done = _apply(case)
    row = case.storage.catalog_entry_get("existing-catalogue")
    assert row["title"] == "Personal title" and row["metadata"]["note"] == "retain this annotation"
    plan = _run(case, undo_operation=done["operation_id"])
    assert _run(case, undo_operation=done["operation_id"], confirmation=plan["plan_token"])["status"] == "undone"
    after = _dump(case)
    assert all(after[table] == before[table] for table in before if table != "metadata")


@pytest.mark.parametrize("foreign", ["identity", "product", "artifact"])
def test_existing_conflicting_placement_is_never_reassigned(case, foreign):
    row = dict(path=str(case.target), identity_id="default", provider_id="kobo",
               source_item_id="PRIVATE-product", artifact_id="sha256:" + case.sha)
    if foreign == "identity":
        row["identity_id"] = "another-reader"
    elif foreign == "product":
        row["source_item_id"] = "another-product"
    else:
        row["artifact_id"] = "sha256:" + "0" * 64
    case.storage.placement_put(row)
    before = _dump(case)
    with pytest.raises(repair.ReconciliationRejected):
        _run(case)
    assert _dump(case) == before


def test_permission_denied_is_fixed_and_does_not_mutate(case, monkeypatch):
    original = repair.os.open
    def denied(path, *args, **kwargs):
        if str(path) == case.target.name:
            raise PermissionError("PRIVATE path")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(repair.os, "open", denied)
    before = _dump(case)
    with pytest.raises(repair.ReconciliationRejected, match="^file_unverified$"):
        _run(case)
    assert _dump(case) == before


def test_missing_generic_artifact_or_cache_never_fabricates_evidence(case):
    with case.storage._lock, case.storage._connect() as db:
        db.execute("DELETE FROM artifacts")
    before = _dump(case)
    with pytest.raises(repair.ReconciliationRejected, match="artifact_evidence_missing"):
        _run(case)
    assert _dump(case) == before


def test_legacy_uppercase_sha_is_verified_without_rewriting_the_attestation(case):
    with case.storage._lock, case.storage._connect() as db:
        db.execute("UPDATE backup_items SET sha256=?", (case.sha.upper(),))
    assert _apply(case)["status"] == "applied"
    assert case.storage.backup_get("kobo", "vault-1", "PRIVATE-product")["sha256"] == case.sha.upper()


def _native(case, **options):
    return repair.reconcile_existing_profile(config_path=case.service.config.path,
                                            database_path=case.storage.path,
                                            **{**case.options, **options})


def test_native_preview_does_not_chmod_config_purge_cache_or_create_lock(case):
    case.service.config.save()
    case.service.config.path.chmod(0o640)
    config_before = case.service.config.path.stat()
    contents = case.service.config.path.read_bytes()
    before = _dump(case)
    assert _native(case)["dry_run"]
    assert _dump(case) == before
    after = case.service.config.path.stat()
    assert (after.st_mode, after.st_mtime_ns, after.st_ctime_ns) == (config_before.st_mode, config_before.st_mtime_ns, config_before.st_ctime_ns)
    assert case.service.config.path.read_bytes() == contents
    assert not (case.tmp / "acquisition.lock").exists()


def test_missing_profile_is_not_created_as_a_side_effect(case, monkeypatch):
    root = case.tmp / "absent-profile"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(root / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(root / "data"))
    with pytest.raises(repair.ReconciliationRejected, match="local_profile_unavailable"):
        repair.reconcile_existing_profile(**case.options)
    assert not root.exists()


def test_native_preview_does_not_migrate_an_old_database(case):
    case.service.config.save()
    case.storage.metadata_set("schema_version", "12")
    before = _dump(case)
    with pytest.raises(repair.ReconciliationRejected, match="schema_upgrade_required"):
        _native(case)
    assert _dump(case) == before


def test_database_read_only_error_cannot_leave_partial_links(case, monkeypatch):
    plan = _run(case)
    before = _dump(case)
    readonly = sqlite3.connect(case.storage.path.absolute().as_uri() + "?mode=ro", uri=True)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(case.storage, "_connect", lambda: readonly)
            with pytest.raises(repair.ReconciliationRejected, match="local_storage_unavailable"):
                _run(case, confirmation=plan["plan_token"])
    finally:
        readonly.close()
    assert _dump(case) == before


def test_pending_directory_migration_is_not_recovered_and_no_file_is_opened(case, monkeypatch):
    case.service.config.save()
    case.storage.metadata_set("identity-directory-migration-v1", '{"phase":"prepared"}')
    monkeypatch.setattr(repair, "_read_hash_within", lambda *args: pytest.fail("No hash while a migration is pending"))
    before = _dump(case)
    with pytest.raises(repair.ReconciliationRejected, match="directory_migration_pending"):
        _native(case)
    assert _dump(case) == before


def test_native_apply_rechecks_configuration_from_disk_before_committing(case, monkeypatch):
    case.service.config.save()
    plan = _native(case)
    before = _dump(case)
    original = repair._write_row
    def write(db, table, row, previous):
        original(db, table, row, previous)
        if table == "catalog_entries":
            data = json.loads(case.service.config.path.read_text())
            data["library"]["directory"] = str(case.tmp / "other-library")
            case.service.config.path.write_text(json.dumps(data))
    monkeypatch.setattr(repair, "_write_row", write)
    with pytest.raises(repair.ReconciliationRejected, match="source_configuration_changed"):
        _native(case, confirmation=plan["plan_token"])
    assert _dump(case) == before


def test_all_sql_writers_are_serialized_during_reconciliation(case, monkeypatch):
    plan = _run(case)
    original = repair._write_row
    attempts = []
    def write(db, table, row, previous):
        original(db, table, row, previous)
        if not attempts:
            other = sqlite3.connect(case.storage.path, timeout=0)
            try:
                with pytest.raises(sqlite3.OperationalError, match="locked"):
                    other.execute("UPDATE entitlements SET state='expired'")
                attempts.append(True)
            finally:
                other.close()
    monkeypatch.setattr(repair, "_write_row", write)
    assert _run(case, confirmation=plan["plan_token"])["status"] == "applied" and attempts


def test_metadata_state_flip_cannot_falsely_claim_undo_completed(case):
    done = _apply(case)
    key = repair._JOURNAL + done["operation_id"]
    data = json.loads(case.storage.metadata_get(key))
    data["state"] = "undone"
    case.storage.metadata_set(key, json.dumps(data))
    before = _dump(case)
    with pytest.raises(repair.ReconciliationRejected, match="journal_state_conflict"):
        _run(case, undo_operation=done["operation_id"])
    assert _dump(case) == before


def test_oversize_evidence_is_blocked_before_returning_a_confirmation_plan(case):
    with case.storage._lock, case.storage._connect() as db:
        db.execute("UPDATE backup_items SET title=?", ("X" * 262144,))
    before = _dump(case)
    with pytest.raises(repair.ReconciliationRejected, match="journal_budget_exhausted"):
        _run(case)
    assert _dump(case) == before


def test_shared_acquisition_lock_blocks_confirmation_but_not_dry_run(case):
    plan = _run(case)
    before = _dump(case)
    with InterProcessLock(case.tmp / "acquisition.lock"):
        assert _run(case)["dry_run"]
        with pytest.raises(repair.ReconciliationRejected, match="acquisition_busy"):
            _run(case, confirmation=plan["plan_token"])
    assert _dump(case) == before


def test_sql_failure_in_middle_rolls_back_every_projection_and_journal(case):
    plan = _run(case)
    with case.storage._lock, case.storage._connect() as db:
        db.execute("CREATE TRIGGER reject_catalog BEFORE INSERT ON catalog_entries BEGIN SELECT RAISE(ABORT, 'PRIVATE SQL detail'); END")
    before = _dump(case)
    with pytest.raises(repair.ReconciliationRejected, match="local_storage_unavailable"):
        _run(case, confirmation=plan["plan_token"])
    assert _dump(case) == before


def test_changed_file_during_transaction_rolls_back_database(case, monkeypatch):
    plan = _run(case)
    before = _dump(case)
    original = repair._write_row
    def write(db, table, row, previous):
        original(db, table, row, previous)
        if table == "catalog_entries":
            case.target.write_bytes(b"external modification")
    monkeypatch.setattr(repair, "_write_row", write)
    with pytest.raises(repair.ReconciliationRejected, match="file_hash_mismatch"):
        _run(case, confirmation=plan["plan_token"])
    assert _dump(case) == before and case.original.is_file()


def test_undo_refuses_newer_catalogue_edit_instead_of_losing_it(case):
    done = _apply(case)
    placement = case.storage.placement_for_path(case.target)
    with case.storage._lock, case.storage._connect() as db:
        db.execute("UPDATE catalog_entries SET title='New user annotation' WHERE placement_id=?", (placement["placement_id"],))
    before = _dump(case)
    with pytest.raises(repair.ReconciliationRejected, match="state_changed"):
        _run(case, undo_operation=done["operation_id"])
    assert _dump(case) == before


@pytest.mark.parametrize("poison", ["sha", "path", "table"])
def test_untrusted_journal_cannot_rewrite_other_evidence(case, poison):
    done = _apply(case)
    key = repair._JOURNAL + done["operation_id"]
    data = json.loads(case.storage.metadata_get(key))
    if poison == "sha":
        data["before"]["backup_items"]["sha256"] = "0" * 64
    elif poison == "path":
        data["before"]["backup_items"]["local_path"] = "/PRIVATE-other-reader.epub"
    else:
        data["before"]["entitlements"] = data["watch"]["entitlements"]
    case.storage.metadata_set(key, json.dumps(data))
    before = _dump(case)
    with pytest.raises(repair.ReconciliationRejected, match="invalid_journal"):
        _run(case, undo_operation=done["operation_id"])
    assert _dump(case) == before


def test_cli_is_offline_and_expurgates_success_and_unexpected_errors(case, monkeypatch, capsys):
    case.service.config.save()
    monkeypatch.setattr(repair, "config_file", lambda **kwargs: case.service.config.path)
    monkeypatch.setattr(repair, "database_file", lambda **kwargs: case.storage.path)
    monkeypatch.setattr(cli.CiferecaCore, "open", lambda **kwargs: pytest.fail("No Core/plugin startup"))
    args = ["kobo-reconcile-local", "--credential", "vault-1", "--account-id", "PRIVATE-account",
            "--item", "PRIVATE-product", "--sha256", case.sha, "--path", str(case.target)]
    assert cli.main(args) == 0
    output = capsys.readouterr()
    token = json.loads(output.out)["plan_token"]
    assert not output.err and "PRIVATE" not in output.out and case.sha not in output.out
    assert cli.main(args + ["--confirm", token]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "applied"
    def fail(*args, **kwargs):
        raise RuntimeError("Bearer PRIVATE token /secret/path")
    monkeypatch.setattr(repair, "reconcile_existing_profile", fail)
    assert cli.main(args) == 2
    output = capsys.readouterr()
    assert json.loads(output.out)["reason"] == "reconciliation_unavailable"
    assert "PRIVATE" not in output.out + output.err


def test_real_cli_process_preview_apply_and_undo_preserve_files_and_cache(case):
    root = case.tmp / "cli-profile"
    config_path = root / "config/cifereca/config.json"
    database_path = root / "data/cifereca/library.sqlite3"
    config_path.parent.mkdir(parents=True)
    database_path.parent.mkdir(parents=True)
    config_path.write_text(json.dumps(case.service.config.data))
    config_path.chmod(0o640)
    with case.storage._lock, case.storage._connect() as db:
        db.execute("INSERT INTO cache(namespace,cache_key,payload,stored_at,expires_at) VALUES('expired-test','sentinel','{}',0,1)")
    destination = sqlite3.connect(database_path)
    try:
        case.storage._connect().backup(destination)
    finally:
        destination.close()
    env = {**os.environ, "XDG_CONFIG_HOME": str(root / "config"),
           "XDG_DATA_HOME": str(root / "data"), "XDG_STATE_HOME": str(root / "state")}
    args = ["kobo-reconcile-local", "--credential", "vault-1", "--account-id", "PRIVATE-account",
            "--item", "PRIVATE-product", "--sha256", case.sha, "--path", str(case.target)]
    def command(extra=()):
        result = subprocess.run([sys.executable, "-m", "cifereca.cli", *args, *extra],
                                env=env, capture_output=True, text=True, timeout=20)
        assert result.returncode == 0, result.stderr + result.stdout
        assert not result.stderr
        assert all(value not in result.stdout for value in (case.sha, "PRIVATE", str(case.tmp)))
        return json.loads(result.stdout)
    files = {p: p.read_bytes() for p in (case.original, case.target)}
    config_stat = config_path.stat()
    preview = command()
    assert preview["dry_run"] and not (root / "state").exists()
    applied = command(["--confirm", preview["plan_token"]])
    assert applied["status"] == "applied"
    undo = command(["--undo", applied["operation_id"]])
    assert command(["--undo", applied["operation_id"], "--confirm", undo["plan_token"]])["status"] == "undone"
    assert command(["--undo", applied["operation_id"]])["status"] == "already_undone"
    with sqlite3.connect(database_path) as db:
        assert db.execute("SELECT COUNT(*) FROM cache WHERE namespace='expired-test'").fetchone()[0] == 1
        assert db.execute("SELECT local_path,status FROM backup_items").fetchone() == (str(case.original), "reconcile_required")
    assert config_path.stat().st_mode == config_stat.st_mode
    assert config_path.stat().st_mtime_ns == config_stat.st_mtime_ns
    assert all(path.read_bytes() == data for path, data in files.items())


def test_abrupt_process_exit_never_commits_half_a_reconciliation(case):
    plan = _run(case)
    before = _dump(case)
    # The child opens the existing disposable database; exit occurs after its
    # first real SQL write, with a real WAL transaction and acquisition lock.
    script = '''
import os,json,sys
from types import SimpleNamespace as NS
from pathlib import Path
from cifereca.config import Config
from cifereca.storage import Storage
from cifereca.identities import Identity
from cifereca.core import kobo_local_reconciliation as m
options=json.loads(sys.argv[2]);root=Path(sys.argv[1]);config=Config(root/'child-config.json')
config.set('library.directory',str(root/'EBooks'))
identity=Identity('default','Hugues',directory_name='hugues')
service=NS(config=config,storage=Storage(root/'db.sqlite3'),identity_by_id=lambda i: identity if i=='default' else None)
m.acquisition_lock_file=lambda:root/'acquisition.lock'
original=m._write_row
def crash(db,table,row,previous):
 original(db,table,row,previous)
 os._exit(17)
m._write_row=crash
m.reconcile_kobo_local(service,**options)
'''
    result = subprocess.run([sys.executable, "-c", script, str(case.tmp),
                             json.dumps({**case.options, "confirmation": plan["plan_token"]})],
                            env=os.environ.copy(), capture_output=True, text=True, timeout=20)
    assert result.returncode == 17, result.stderr
    assert _dump(case) == before
    assert _run(case, confirmation=plan["plan_token"])["status"] == "applied"
