"""P0 #5069 A1: read-only recovery diagnosis, fail-closed pre-network gate.

Synthetic runtime only: no GitHub, real plugin activation, or user data.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from cifereca import updater


TX_ID = "20261108T101214Z-2b88f133f09d"


def _broken_previous(tmp_path: Path, monkeypatch, *, missing: str = "previous"):
    root = tmp_path / "runtime"
    release = root / "releases" / "safe-current"
    release.mkdir(parents=True)
    gone = root / "releases" / "deleted-previous"
    if missing == "current":
        missing_current, missing_previous = gone, None
        updater._replace_symlink(root / "current", gone)
    else:
        missing_current, missing_previous = release, gone
        updater._replace_symlink(root / "current", release)
    folder = root / "transactions" / TX_ID
    folder.mkdir(parents=True)
    transaction = {
        "schema": updater.TRANSACTION_SCHEMA,
        "id": TX_ID,
        "state": "restoration_incomplete",
        "restoration_errors": ["verification"],
        "core": {
            "before_current": {"path": str(missing_current)},
            "before_previous": (
                {"path": str(missing_previous)} if missing_previous else None
            ),
        },
        "packages": {},
    }
    updater._write_json_atomic(folder / "transaction.json", transaction)
    monkeypatch.setenv("CIFERECA_INSTALL_ROOT", str(root))
    monkeypatch.setattr(updater, "_plugin_packages_root", lambda: tmp_path / "packages")
    return root, gone, folder


def test_missing_previous_is_typed_read_only_and_redacted(tmp_path, monkeypatch):
    root, gone, folder = _broken_previous(tmp_path, monkeypatch)
    original = (folder / "transaction.json").read_bytes()
    links = {name: os.readlink(root / name) if (root / name).is_symlink() else None
             for name in ("current", "previous")}
    result = updater.recovery_inspect()
    assert result["ok"] is True and result["read_only"] is True
    assert result["blocked"] is True
    assert result["incomplete_transactions"] == [{
        "id": TX_ID,
        "state": "restoration_incomplete",
        "reasons": ["before_previous_missing"],
        "action": "restore_missing_evidence",
    }]
    assert str(root) not in json.dumps(result)
    assert str(gone) not in json.dumps(result)
    assert (folder / "transaction.json").read_bytes() == original
    assert {name: os.readlink(root / name) if (root / name).is_symlink() else None
            for name in ("current", "previous")} == links
    assert not gone.exists()


def test_github_preflight_fails_before_checkout_and_wheelhouse(tmp_path, monkeypatch):
    root, gone, folder = _broken_previous(tmp_path, monkeypatch)
    def forbidden(*_args, **_kwargs):
        pytest.fail("GitHub checkout, fetch, or build was reached")

    monkeypatch.setattr(updater, "_github_checkout", forbidden)
    monkeypatch.setattr(updater, "_prepare_github_wheelhouse", forbidden)
    with pytest.raises(updater.UpdateError, match="before_previous_missing") as error:
        updater.install_github("main")
    assert TX_ID in str(error.value)
    assert str(root) not in str(error.value)
    assert (folder / "transaction.json").exists()
    assert not gone.exists()


def test_archive_and_source_gate_before_resolution_or_metadata(tmp_path, monkeypatch):
    _broken_previous(tmp_path, monkeypatch)
    def forbidden(*_args, **_kwargs):
        pytest.fail("source or archive admission should not run while blocked")

    monkeypatch.setattr(updater, "resolve_archive", forbidden)
    monkeypatch.setattr(updater, "_metadata", forbidden)
    with pytest.raises(updater.UpdateError, match="before_previous_missing"):
        updater.install_archive(tmp_path / "not-present.zip")
    with pytest.raises(updater.UpdateError, match="before_previous_missing"):
        updater.install_source(tmp_path / "not-present")


def test_missing_before_current_is_not_reconciled(tmp_path, monkeypatch):
    root, gone, _folder = _broken_previous(tmp_path, monkeypatch, missing="current")
    result = updater.recovery_inspect()
    reasons = result["incomplete_transactions"][0]["reasons"]
    assert "before_current_missing" in reasons
    with pytest.raises(updater.UpdateError, match="before_current_missing"):
        updater._preflight_incomplete_recovery(root)
    assert not gone.exists()


def test_missing_previous_restored_removes_irrecoverable_preflight(tmp_path, monkeypatch):
    root, gone, folder = _broken_previous(tmp_path, monkeypatch)
    with pytest.raises(updater.UpdateError):
        updater._preflight_incomplete_recovery(root)
    gone.mkdir()
    # The existing transactional recover still has work to do (previous
    # link). It must not be mistaken for a successful restoration receipt.
    inspected = updater.recovery_inspect()
    assert "core_link_mismatch" in inspected["incomplete_transactions"][0]["reasons"]
    updater._preflight_incomplete_recovery(root)
    assert updater._read_transaction(folder)["state"] == "restoration_incomplete"


def test_recovery_inspect_cli_json_is_safe(tmp_path, monkeypatch, capsys):
    root, gone, folder = _broken_previous(tmp_path, monkeypatch)
    assert updater.main(["--recovery-inspect", "--json"]) == 0
    captured = capsys.readouterr()
    value = json.loads(captured.out)
    assert value["read_only"] is True
    assert value["incomplete_transactions"][0]["id"] == TX_ID
    assert "before_previous_missing" in captured.out
    assert str(root) not in captured.out
    assert captured.err == ""


def test_symlinked_transaction_directory_is_never_traversed(tmp_path, monkeypatch):
    root = tmp_path / "runtime"
    transactions = root / "transactions"
    transactions.mkdir(parents=True)
    outside = tmp_path / "private"
    outside.mkdir()
    (outside / "transaction.json").write_text("not json", encoding="utf-8")
    (transactions / TX_ID).symlink_to(outside, target_is_directory=True)
    report = updater.recovery_inspect(root)
    assert report["incomplete_transactions"][0]["reasons"] == [
        "unsafe_transaction_directory",
    ]
    with pytest.raises(updater.UpdateError, match="unsafe_transaction_directory"):
        updater._preflight_incomplete_recovery(root)
    assert (outside / "transaction.json").read_text(encoding="utf-8") == "not json"


def test_completed_transactions_are_not_false_blockers(tmp_path, monkeypatch):
    root, gone, folder = _broken_previous(tmp_path, monkeypatch)
    saved = updater._read_transaction(folder)
    saved["state"] = "update_failed_restored"
    updater._write_json_atomic(folder / "transaction.json", saved)
    inspected = updater.recovery_inspect()
    assert inspected["blocked"] is False
    assert inspected["incomplete_transactions"] == []
    updater._preflight_incomplete_recovery(root)
