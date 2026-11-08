"""P0 #5069 A2: supervised and auditable lost-previous reconciliation.

Offline synthetic evidence only: never access a real profile, library or plugin.
"""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from cifereca import updater


ID = "20261108T101214Z-2b88f133f09d"


def _scenario(tmp_path, monkeypatch):
    root = tmp_path / "runtime"
    current = root / "releases" / "1.0.0-existing"
    current.mkdir(parents=True)
    (current / ".cifereca-install.json").write_text(
        '{"version": "1.0.0", "provenance": {"commit": "known"}}\n',
        encoding="utf-8",
    )
    missing = root / "releases" / "0.8.0-deleted"
    updater._replace_symlink(root / "current", current)
    folder = root / "transactions" / ID
    folder.mkdir(parents=True)
    tx = {
        "schema": updater.TRANSACTION_SCHEMA,
        "id": ID,
        "state": "restoration_incomplete",
        "restoration_errors": ["verification"],
        "core": {
            "before_current": updater._core_identity(current),
            "before_previous": updater._core_identity(missing),
        },
        "packages": {},
    }
    updater._write_json_atomic(folder / "transaction.json", tx)
    monkeypatch.setenv("CIFERECA_INSTALL_ROOT", str(root))
    monkeypatch.setattr(updater, "_plugin_packages_root", lambda: tmp_path / "packages")
    return root, folder, missing


def test_plan_is_read_only_and_does_not_clear_incomplete_state(tmp_path, monkeypatch):
    root, folder, missing = _scenario(tmp_path, monkeypatch)
    original = (folder / "transaction.json").read_bytes()
    plan = updater.reconcile_lost_previous(ID)
    assert plan["read_only"] is True
    assert plan["reason"] == "before_previous_missing"
    assert plan["proposed_state"] == "reconciled_degraded"
    assert len(plan["acknowledgement_sha256"]) == 64
    assert not (root / "reconciliation-audit").exists()
    assert (folder / "transaction.json").read_bytes() == original
    assert not missing.exists()
    assert str(root) not in json.dumps(plan)


def test_apply_requires_exact_ack_and_persists_private_original_audit(tmp_path, monkeypatch):
    root, folder, missing = _scenario(tmp_path, monkeypatch)
    original = updater._read_transaction(folder)
    plan = updater.reconcile_lost_previous(ID)
    result = updater.reconcile_lost_previous(
        ID, acknowledge=plan["acknowledgement_sha256"],
    )
    assert result["state"] == "reconciled_degraded"
    assert result["no_previous_release_restored"] is True
    updated = updater._read_transaction(folder)
    assert updated["state"] == "reconciled_degraded"
    assert updated["core"] == original["core"]
    assert updated["packages"] == original["packages"]
    assert updated["reconciliation"]["reason"] == "before_previous_missing"
    audit = root / "reconciliation-audit" / result["audit"]
    assert audit.is_file()
    assert stat.S_IMODE(audit.stat().st_mode) == 0o600
    assert stat.S_IMODE(audit.parent.stat().st_mode) == 0o700
    receipt = json.loads(audit.read_text(encoding="utf-8"))
    assert receipt["original_transaction"] == original
    assert receipt["acknowledgement_sha256"] == plan["acknowledgement_sha256"]
    assert not missing.exists()
    assert updater._symlink_target(root / "previous") is None
    assert updater._recover_incomplete_transactions(root) == []
    assert updater.recovery_inspect()["blocked"] is False
    assert updater._gc(root, 2)["deferred"] == 0


def test_wrong_ack_refuses_all_mutation(tmp_path, monkeypatch):
    root, folder, missing = _scenario(tmp_path, monkeypatch)
    original = (folder / "transaction.json").read_bytes()
    with pytest.raises(updater.UpdateError, match="reconciliation_evidence_changed"):
        updater.reconcile_lost_previous(ID, acknowledge="0" * 64)
    assert (folder / "transaction.json").read_bytes() == original
    assert not (root / "reconciliation-audit").exists()


def test_plan_expires_after_core_symlink_change(tmp_path, monkeypatch):
    root, folder, missing = _scenario(tmp_path, monkeypatch)
    proof = updater.reconcile_lost_previous(ID)["acknowledgement_sha256"]
    outsider = root / "releases" / "other"
    outsider.mkdir()
    updater._replace_symlink(root / "current", outsider)
    with pytest.raises(updater.UpdateError, match="reconciliation_evidence_not_unique"):
        updater.reconcile_lost_previous(ID, acknowledge=proof)
    assert updater._read_transaction(folder)["state"] == "restoration_incomplete"


def test_missing_current_is_never_reconciled(tmp_path, monkeypatch):
    root, folder, missing = _scenario(tmp_path, monkeypatch)
    current = updater._symlink_target(root / "current")
    assert current is not None
    current.rename(root / "releases" / "moved")
    with pytest.raises(updater.UpdateError, match="reconciliation_evidence_not_unique"):
        updater.reconcile_lost_previous(ID)
    assert updater._read_transaction(folder)["state"] == "restoration_incomplete"


def test_plugin_drift_or_missing_snapshot_cannot_be_reconciled(tmp_path, monkeypatch):
    root, folder, missing = _scenario(tmp_path, monkeypatch)
    tx = updater._read_transaction(folder)
    tx["packages"] = {
        "org.safety": {
            "before": {"exists": False, "active": None, "versions": [],
                       "sha256": None, "files": {}},
            "after": None,
        },
    }
    updater._write_json_atomic(folder / "transaction.json", tx)
    pkg = tmp_path / "packages" / "org.safety"
    pkg.mkdir(parents=True)
    (pkg / "secret").write_bytes(b"not-the-before-state")
    with pytest.raises(updater.UpdateError, match="reconciliation_evidence_not_unique"):
        updater.reconcile_lost_previous(ID)
    assert updater._read_transaction(folder)["state"] == "restoration_incomplete"


def test_extra_incomplete_transaction_refuses_reconciliation(tmp_path, monkeypatch):
    root, folder, missing = _scenario(tmp_path, monkeypatch)
    second = root / "transactions" / "20261108T111111Z-112233445566"
    second.mkdir()
    tx = updater._read_transaction(folder)
    tx["id"] = second.name
    tx["state"] = "prepared"
    updater._write_json_atomic(second / "transaction.json", tx)
    with pytest.raises(updater.UpdateError, match="reconciliation_evidence_not_unique"):
        updater.reconcile_lost_previous(ID)


def test_cli_plan_then_apply_without_git_or_plugin_execution(tmp_path, monkeypatch, capsys):
    root, folder, missing = _scenario(tmp_path, monkeypatch)
    assert updater.main(["--recovery-reconcile", ID, "--json"]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["read_only"] is True
    assert updater.main([
        "--recovery-reconcile", ID,
        "--acknowledge", plan["acknowledgement_sha256"], "--json",
    ]) == 0
    reply = json.loads(capsys.readouterr().out)
    assert reply["state"] == "reconciled_degraded"
    assert updater._read_transaction(folder)["state"] == "reconciled_degraded"


def test_operation_lock_is_reentrant_within_process(tmp_path, monkeypatch):
    root, folder, missing = _scenario(tmp_path, monkeypatch)
    with updater._exclusive_updater_lock(root):
        with updater._exclusive_updater_lock(root):
            assert (root / ".cifereca-updater.lock").exists()
    assert updater.reconcile_lost_previous(ID)["ok"] is True


def test_crash_after_durable_audit_can_resume_without_replacing_receipt(tmp_path, monkeypatch):
    root, folder, _missing = _scenario(tmp_path, monkeypatch)
    proof = updater.reconcile_lost_previous(ID)["acknowledgement_sha256"]
    original_save = updater._save_transaction

    def simulated_crash(*_args, **_kwargs):
        raise RuntimeError("synthetic crash after audit before transaction state")

    monkeypatch.setattr(updater, "_save_transaction", simulated_crash)
    with pytest.raises(RuntimeError, match="synthetic crash"):
        updater.reconcile_lost_previous(ID, acknowledge=proof)
    assert updater._read_transaction(folder)["state"] == "restoration_incomplete"
    audit = next((root / "reconciliation-audit").glob("*.json"))
    exact_receipt = audit.read_bytes()
    monkeypatch.setattr(updater, "_save_transaction", original_save)
    resumed = updater.reconcile_lost_previous(ID, acknowledge=proof)
    assert resumed["state"] == "reconciled_degraded"
    assert audit.read_bytes() == exact_receipt
    assert updater._read_transaction(folder)["state"] == "reconciled_degraded"


def test_partial_previous_audit_must_not_be_overwritten(tmp_path, monkeypatch):
    root, folder, _missing = _scenario(tmp_path, monkeypatch)
    proof = updater.reconcile_lost_previous(ID)["acknowledgement_sha256"]
    audit_root = root / "reconciliation-audit"
    audit_root.mkdir(mode=0o700)
    audit = audit_root / (ID + "-" + proof[:16] + ".json")
    audit.write_text('{"schema":', encoding="utf-8")
    audit.chmod(0o600)  # Isolate corrupt JSON, not a separate permissions refusal.
    original = audit.read_bytes()
    with pytest.raises(updater.UpdateError, match="reconciliation_existing_audit_unreadable"):
        updater.reconcile_lost_previous(ID, acknowledge=proof)
    assert audit.read_bytes() == original
    assert updater._read_transaction(folder)["state"] == "restoration_incomplete"


def test_audit_symlink_rejected_without_touching_external_file(tmp_path, monkeypatch):
    root, folder, _missing = _scenario(tmp_path, monkeypatch)
    proof = updater.reconcile_lost_previous(ID)["acknowledgement_sha256"]
    audit_root = root / "reconciliation-audit"
    audit_root.mkdir(mode=0o700)
    outside = tmp_path / "private-receipt"
    outside.write_text("unchanged", encoding="utf-8")
    (audit_root / (ID + "-" + proof[:16] + ".json")).symlink_to(outside)
    with pytest.raises(updater.UpdateError, match="reconciliation_existing_audit_unsafe"):
        updater.reconcile_lost_previous(ID, acknowledge=proof)
    assert outside.read_text(encoding="utf-8") == "unchanged"
    assert updater._read_transaction(folder)["state"] == "restoration_incomplete"


def test_os_lock_excludes_independent_process(tmp_path, monkeypatch):
    import subprocess
    import sys

    root, _folder, _missing = _scenario(tmp_path, monkeypatch)
    lockfile = root / ".cifereca-updater.lock"
    child = (
        "import fcntl,os,sys\n"
        "fd=os.open(sys.argv[1], os.O_RDWR | os.O_NOFOLLOW)\n"
        "try:\n"
        "  fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
        "except BlockingIOError:\n"
        "  sys.exit(23)\n"
        "sys.exit(0)\n"
    )
    with updater._exclusive_updater_lock(root):
        competing = subprocess.run(
            [sys.executable, "-c", child, str(lockfile)],
            timeout=10, capture_output=True,
        )
        assert competing.returncode == 23
    next_process = subprocess.run(
        [sys.executable, "-c", child, str(lockfile)],
        timeout=10, capture_output=True,
    )
    assert next_process.returncode == 0


@pytest.mark.parametrize("unfinished_state", ["prepared", "applying", "restoring", "rolling_back"])
def test_missing_rollback_generation_blocks_every_unfinished_state_before_restore(
    tmp_path, monkeypatch, unfinished_state,
):
    root, folder, missing = _scenario(tmp_path, monkeypatch)
    saved = updater._read_transaction(folder)
    saved["state"] = unfinished_state
    updater._write_json_atomic(folder / "transaction.json", saved)
    before = (folder / "transaction.json").read_bytes()

    def forbidden_restore(*_args, **_kwargs):
        pytest.fail("Automatic destructive recovery must not start")

    monkeypatch.setattr(updater, "_restore_transaction_packages", forbidden_restore)
    monkeypatch.setattr(updater, "_restore_release_links", forbidden_restore)
    with pytest.raises(updater.UpdateError, match="before_previous_missing"):
        updater._preflight_incomplete_recovery(root)
    assert (folder / "transaction.json").read_bytes() == before
    assert not missing.exists()
