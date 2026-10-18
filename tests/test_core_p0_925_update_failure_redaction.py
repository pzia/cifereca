"""P0 #925: updater errors must not echo untrusted subprocess output or exceptions."""
from __future__ import annotations

import json
import stat
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from cifereca import dev_updater, updater


SECRET = "https://private.invalid/?token=NEVER_EXPOSE_THIS"


@pytest.mark.parametrize("which", ["snapshot_build", "plugin_apply", "restored_smoke"])
def test_failed_subprocess_never_surfaces_captured_output(monkeypatch, tmp_path, which):
    monkeypatch.setattr(
        updater.subprocess, "run",
        lambda *_a, **_kw: SimpleNamespace(returncode=1, stdout=SECRET, stderr=SECRET),
    )
    monkeypatch.setattr(updater, "_command_path", lambda *_a: tmp_path / "cli")
    (tmp_path / "cli").touch()
    if which == "snapshot_build":
        monkeypatch.setattr(updater, "_release_plugin_manifest", lambda *_a: {"org.demo": "1"})
        (tmp_path / "src" / "plugins").mkdir(parents=True)
        script = tmp_path / "scripts" / "package-dev-snapshot.py"
        script.parent.mkdir()
        script.touch()
        invoke = lambda: updater._build_release_plugin_snapshot(tmp_path, tmp_path / "out")
    elif which == "plugin_apply":
        invoke = lambda: updater._apply_release_plugin_snapshot(tmp_path, tmp_path / "snapshot.zip")
    else:
        invoke = lambda: updater._smoke_restored_plugins(
            tmp_path, {"packages": {"org.demo": {"before": {"exists": True}}}}
        )
    with pytest.raises(updater.UpdateError) as error:
        invoke()
    assert "NEVER_EXPOSE_THIS" not in str(error.value)
    assert SECRET not in str(error.value)


def test_transaction_creation_does_not_echo_raw_internal_error(monkeypatch, tmp_path):
    def failed_identity(_id):
        raise RuntimeError(SECRET)

    monkeypatch.setattr(updater, "_package_identity", failed_identity)
    release = tmp_path / "release"
    release.mkdir()
    with pytest.raises(updater.UpdateError) as error:
        updater._create_update_transaction(
            tmp_path / "runtime", release, None, None, ["org.demo"]
        )
    assert "Snapshot transactionnel" in str(error.value)
    assert SECRET not in str(error.value)


def test_partial_restore_does_not_persist_exception_details(monkeypatch, tmp_path):
    states = []

    def failed(*_args):
        raise RuntimeError(SECRET)

    monkeypatch.setattr(updater, "_restore_transaction_packages", failed)
    monkeypatch.setattr(updater, "_restore_release_links", failed)
    monkeypatch.setattr(
        updater, "_save_transaction",
        lambda _p, transaction, state: states.append((state, list(transaction.get("restoration_errors", [])))),
    )
    transaction = {"core": {}, "packages": {"org.demo": {}}}
    with pytest.raises(updater.UpdateError) as error:
        updater._complete_transaction_restore(tmp_path, tmp_path, transaction, "restoring")
    assert states[-1] == ("restoration_incomplete", ["plugins", "core"])
    assert SECRET not in str(error.value)
    assert SECRET not in repr(transaction)


@pytest.mark.parametrize("name", ["../NEVER_EXPOSE_THIS", "/tmp/NEVER_EXPOSE_THIS"])
def test_archive_member_paths_are_not_echoed(name):
    info = zipfile.ZipInfo(name)
    with pytest.raises(updater.UpdateError) as core:
        updater._safe_member(info)
    with pytest.raises(dev_updater.DevUpdateError) as dev:
        dev_updater._safe_member(info)
    assert "NEVER_EXPOSE_THIS" not in str(core.value)
    assert "NEVER_EXPOSE_THIS" not in str(dev.value)


def test_invalid_plugin_zip_error_does_not_echo_source_exception(monkeypatch, tmp_path):
    class BrokenZip:
        def __init__(self, *_a, **_kw):
            raise RuntimeError(SECRET)
    monkeypatch.setattr(dev_updater.zipfile, "ZipFile", BrokenZip)
    with pytest.raises(dev_updater.DevUpdateError) as error:
        dev_updater._read_plugin_package_id(tmp_path / "private.zip")
    assert SECRET not in str(error.value)


def test_restart_error_is_closed_code_not_raw_exception(monkeypatch, tmp_path):
    archive = tmp_path / "snapshot.zip"
    with zipfile.ZipFile(archive, "w") as out:
        out.writestr("s/cifereca-dev.json", json.dumps({
            "format": 1,
            "kind": "cifereca-dev-snapshot",
            "id": "test",
            "plugins": [{"path": "demo.zip"}],
        }))
        out.writestr("s/demo.zip", b"test")
    monkeypatch.setattr(dev_updater, "_read_plugin_package_id", lambda *_a: ("org.demo", "1.0"))
    monkeypatch.setattr(dev_updater, "_package_is_active", lambda *_a: None)
    monkeypatch.setattr(dev_updater, "_validate_plugins_only_index", lambda *_a: None)
    monkeypatch.setattr(dev_updater, "_backup_plugins", lambda *_a: None)
    monkeypatch.setattr(dev_updater, "_install_plugin_replace", lambda *_a, **_kw: None)
    monkeypatch.setattr(dev_updater, "_smoke_plugins", lambda *_a: {"plugin_count": 1})
    monkeypatch.setattr(dev_updater, "state_dir", lambda: tmp_path / "state")
    monkeypatch.setattr(updater, "install_root", lambda: tmp_path / "runtime")
    monkeypatch.setattr(updater, "_symlink_target", lambda *_a: None)
    monkeypatch.setattr(dev_updater, "_new_cli", lambda: tmp_path / "cli")
    monkeypatch.setattr(updater, "_command_path", lambda *_a: tmp_path / "bin")
    def blocked_restart(*_args, **_kwargs):
        raise RuntimeError(SECRET)
    monkeypatch.setattr(dev_updater.subprocess, "Popen", blocked_restart)
    result = dev_updater.apply_snapshot(archive, restart=True)
    assert result["ok"] is True
    assert result["restarted"] is False
    assert result["restart_error"] == "redemarrage_impossible"
    assert SECRET not in json.dumps(result)
