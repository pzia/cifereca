"""P0 #925: Ctrl-C is a failed operation with a recoverable rollback and safe JSON.

No subprocess, provider connection, package installation, network or publication
is executed in these tests; all mutation occurs in temporary fixture trees.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from cifereca import dev_updater, updater
from cifereca.update_progress import update_operation
from tests.test_updater import (
    _release_with_plugin_manifest, _transactional_update_fixture,
    _write_plugin_package,
)


PRIVATE = "https://private.invalid/loan?token=SECRET_CTRL_C"


@pytest.mark.parametrize("mode", ["core", "dev"])
def test_interrupt_cli_keeps_one_redacted_json_document_and_failed_terminal(
    monkeypatch, capsys, mode,
):
    def cancelled(*_args, **_kwargs):
        raise KeyboardInterrupt(PRIVATE)

    if mode == "core":
        monkeypatch.setattr(updater, "install_github", cancelled)
        status = updater.main(["--github", "main", "--json"])
    else:
        monkeypatch.setattr(dev_updater, "apply_snapshot", cancelled)
        status = dev_updater.main(["dev.zip", "--json"])
    result = capsys.readouterr()
    assert status == 130
    payload = json.loads(result.out)
    assert payload == {
        "ok": False,
        "error": {"code": "interrupted", "message": "Mise à jour interrompue"},
    }
    assert result.out.count('"ok"') == 1
    assert result.err.count("reason=interrupted") == 1
    assert "outcome=failed" in result.err
    assert "SECRET_CTRL_C" not in result.out + result.err


def test_interrupted_transactional_core_update_restores_previous_core_and_plugins(
    tmp_path, monkeypatch,
):
    root, _old, packages, baseline = _transactional_update_fixture(tmp_path, monkeypatch)
    current_before = updater._symlink_target(root / "current")
    before_managed = updater._package_identity("org.test")
    before_third_party = updater._package_identity("org.third-party")
    source = _release_with_plugin_manifest(tmp_path / "cancelled-source", "3.0.0")

    applied = []
    def interrupted_plugin_activation(*_args):
        applied.append(True)
        updater.shutil.rmtree(packages / "org.test")
        _write_plugin_package(packages, "org.test", "1.0.0", b"partially replaced", "1.0.0")
        raise KeyboardInterrupt(PRIVATE)
    monkeypatch.setattr(updater, "_apply_release_plugin_snapshot", interrupted_plugin_activation)

    events = []
    with pytest.raises(KeyboardInterrupt):
        with update_operation("source", callback=events.append):
            updater.install_source(source)
    assert applied == [True]
    assert updater._symlink_target(root / "current").resolve() == current_before.resolve()
    assert updater._package_identity("org.test") == before_managed
    assert updater._package_identity("org.third-party") == before_third_party
    assert not (packages / "org.test" / "1.0.0" / "payload.bin").read_bytes() == b"partially replaced"
    transactions = sorted((root / "transactions").iterdir())
    states = [updater._read_transaction(path)["state"] for path in transactions]
    assert "update_failed_restored" in states
    assert "applied" in states
    assert [row["status"] for row in events if row["stage"] == "rollback"] == [
        "ROLLBACK", "OK",
    ]
    assert events[-1]["status"] == "DONE"
    assert events[-1]["outcome"] == "failed"
    assert any(e.get("reason_code") == "interrupted" for e in events)
    assert "SECRET_CTRL_C" not in repr(events)


def test_interrupted_dev_plugin_update_runs_rollback_before_reraising(
    tmp_path, monkeypatch,
):
    source = tmp_path / "contents"
    source.mkdir()
    (source / "plugin.zip").write_bytes(b"fixture")
    archive = tmp_path / "snapshot.zip"
    archive.write_bytes(b"synthetic snapshot")
    manifest = {"id": "dev-fixture", "core": "", "plugins": [{"path": "plugin.zip"}]}
    monkeypatch.setattr(dev_updater, "_extract_snapshot", lambda *_a: (source, manifest))
    monkeypatch.setattr(dev_updater, "_read_plugin_package_id", lambda *_a: ("org.test", "1.0.0"))
    monkeypatch.setattr(dev_updater, "_package_is_active", lambda *_a: False)
    monkeypatch.setattr(dev_updater, "_validate_plugins_only_index", lambda *_a: None)
    monkeypatch.setattr(dev_updater, "state_dir", lambda: tmp_path / "state")
    monkeypatch.setattr(updater, "install_root", lambda: tmp_path / "runtime")
    monkeypatch.setattr(updater, "_symlink_target", lambda *_a: None)
    backup = Mock()
    restore = Mock()
    install = Mock(side_effect=KeyboardInterrupt(PRIVATE))
    monkeypatch.setattr(dev_updater, "_backup_plugins", backup)
    monkeypatch.setattr(dev_updater, "_restore_plugins", restore)
    monkeypatch.setattr(dev_updater, "_install_plugin_replace", install)

    events = []
    with pytest.raises(KeyboardInterrupt):
        with update_operation("dev_snapshot", callback=events.append):
            dev_updater.apply_snapshot(archive)
    backup.assert_called_once()
    install.assert_called_once()
    restore.assert_called_once()
    assert restore.call_args.args[0] == ["org.test"]
    assert events[-1]["outcome"] == "failed"
    assert [event["status"] for event in events if event["stage"] == "rollback"] == [
        "ROLLBACK", "OK",
    ]
    assert any(event.get("reason_code") == "interrupted" for event in events)
    assert PRIVATE not in repr(events)
