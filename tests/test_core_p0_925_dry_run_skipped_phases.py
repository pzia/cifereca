"""#925: a dry-run distinguishes verified checks from deliberately skipped work.

Real Core/Dev updater entry points with fake metadata and a synthetic ZIP
manifest; no installation, plugin activation, network or release.
"""
from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import Mock

import pytest

from cifereca import updater, dev_updater
from cifereca.update_progress import skip_update_phase, update_operation


CORE_SKIPPED = (
    "recover", "source_copy", "venv", "smoke", "backup",
    "plugin_snapshot", "transaction_prepare", "core_activation",
    "plugin_activation", "transaction_finalize", "cleanup",
)
DEV_SKIPPED = (
    "core_activation", "plugin_backup", "plugin_install",
    "plugin_verification", "smoke", "restart",
)


def _statuses(events):
    return [(row["stage"], row["status"]) for row in events]


def test_source_dry_run_only_validates_metadata_and_python_and_reports_skipped(
    tmp_path, monkeypatch,
):
    source = tmp_path / "synthetic-source"
    source.mkdir()
    runtime = tmp_path / "must-not-exist"
    monkeypatch.setattr(updater, "_metadata", lambda path: ("cifereca", "0.9.44"))
    monkeypatch.setattr(updater, "select_python", lambda _py=None: "/offline/python3")
    monkeypatch.setattr(updater, "install_root", lambda: runtime)
    monkeypatch.setattr(updater, "_copy_source", Mock(side_effect=AssertionError("copy forbidden")))
    monkeypatch.setattr(updater, "_create_venv", Mock(side_effect=AssertionError("venv forbidden")))
    events, stream = [], io.StringIO()
    with update_operation("source", callback=events.append, stream=stream):
        result = updater.install_source(source, dry_run=True)
    assert result["dry_run"] is True
    assert result["version"] == "0.9.44"
    assert not runtime.exists()
    assert _statuses(events) == [
        ("operation", "START"),
        ("metadata", "RUNNING"), ("metadata", "OK"),
        ("python_selection", "RUNNING"), ("python_selection", "OK"),
        *((phase, "SKIPPED") for phase in CORE_SKIPPED),
        ("operation", "DONE"),
    ]
    assert all("SKIPPED" in stream.getvalue() for phase in CORE_SKIPPED)
    assert "percent=" not in stream.getvalue()
    assert events[-1]["outcome"] == "success"


def test_dev_snapshot_dry_run_skips_real_install_and_smoke(tmp_path, monkeypatch):
    archive = tmp_path / "dev-snapshot.zip"
    archive.write_bytes(b"offline preview fixture")
    root = tmp_path / "snapshot"
    root.mkdir()
    (root / "p.zip").write_bytes(b"synthetic plugin ZIP")
    manifest = {
        "id": "synthetic", "core": "",
        "plugins": [{"path": "p.zip", "activate": True}],
    }
    monkeypatch.setattr(dev_updater, "_extract_snapshot", lambda *_args: (root, manifest))
    monkeypatch.setattr(dev_updater, "_read_plugin_package_id", lambda _path: ("org.test", "1.0"))
    monkeypatch.setattr(dev_updater, "_package_is_active", lambda _id: None)
    monkeypatch.setattr(dev_updater, "_validate_plugins_only_index", lambda *_args: None)
    backup = Mock(side_effect=AssertionError("no backup"))
    install = Mock(side_effect=AssertionError("no install"))
    monkeypatch.setattr(dev_updater, "_backup_plugins", backup)
    monkeypatch.setattr(dev_updater, "_install_plugin_replace", install)
    monkeypatch.setattr(dev_updater, "_smoke_plugins",
                        Mock(side_effect=AssertionError("no smoke")))
    events, stream = [], io.StringIO()
    with update_operation("dev_snapshot", callback=events.append, stream=stream):
        result = dev_updater.apply_snapshot(archive, dry_run=True)
    assert result["dry_run"] is True
    assert result["core_changed"] is False
    assert result["plugins"] == ["org.test"]
    backup.assert_not_called()
    install.assert_not_called()
    assert [(row["stage"], row["status"]) for row in events
            if row["status"] == "SKIPPED"] == [
        (phase, "SKIPPED") for phase in DEV_SKIPPED
    ]
    assert all(row["status"] != "OK" for row in events
               if row["stage"] in DEV_SKIPPED)
    assert events[-1]["outcome"] == "success"
    assert "dev_snapshot/plugin_install SKIPPED" in stream.getvalue()


def test_skip_helper_is_inert_without_observer_and_validates_stage_when_active():
    skip_update_phase("plugin_install")
    events = []
    with update_operation("archive", callback=events.append):
        skip_update_phase("venv")
        with pytest.raises(ValueError, match="invalid_update_event"):
            skip_update_phase("private/path/SECRET")
    assert events[-2]["stage"] == "venv" and events[-2]["status"] == "SKIPPED"
    assert events[-1]["outcome"] == "success"
    assert "SECRET" not in repr(events)
