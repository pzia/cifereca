"""#925: per-plugin progress is factual i/N, never a fabricated success counter.

All plugin archives, subprocesses and responses are synthetic and kept in
pytest's temporary tree. No user profiles, GUI, packages or network involved.
"""
from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import Mock

import pytest

from cifereca import dev_updater, updater
from cifereca.update_progress import UpdateProgress, update_operation, update_phase


SECRET = "https://private.invalid/LOAN/book?auth=NEVER_LOG"


def _snapshot(tmp_path, monkeypatch):
    root = tmp_path / "contents"
    root.mkdir()
    for name in ("alpha", "beta", "gamma"):
        (root / (name + ".zip")).write_bytes(b"offline-" + name.encode())
    snapshot = tmp_path / "snapshot.zip"
    snapshot.write_bytes(b"offline-synthetic-snapshot")
    manifest = {
        "id": "synthetic", "core": "",
        "plugins": [{"path": name + ".zip"} for name in ("alpha", "beta", "gamma")],
    }
    monkeypatch.setattr(dev_updater, "_extract_snapshot", lambda *_a: (root, manifest))
    monkeypatch.setattr(
        dev_updater, "_read_plugin_package_id",
        lambda archive: ("org." + archive.stem, "1.0.0"),
    )
    monkeypatch.setattr(dev_updater, "_package_is_active", lambda _id: None)
    monkeypatch.setattr(dev_updater, "_validate_plugins_only_index", lambda *_a: None)
    monkeypatch.setattr(dev_updater, "_backup_plugins", lambda *_a: None)
    monkeypatch.setattr(dev_updater, "_smoke_plugins", lambda *_a: {"plugin_count": 3})
    monkeypatch.setattr(dev_updater, "state_dir", lambda: tmp_path / "state")
    monkeypatch.setattr(updater, "install_root", lambda: tmp_path / "runtime")
    monkeypatch.setattr(updater, "_symlink_target", lambda *_a: None)
    return snapshot


def test_dev_install_real_manifest_emits_three_explicit_ordinals(tmp_path, monkeypatch):
    snapshot = _snapshot(tmp_path, monkeypatch)
    install = Mock()
    monkeypatch.setattr(dev_updater, "_install_plugin_replace", install)
    events, output = [], io.StringIO()
    with update_operation("dev_snapshot", callback=events.append, stream=output):
        result = dev_updater.apply_snapshot(snapshot)

    assert result["ok"] is True
    assert result["plugins"] == ["org.alpha", "org.beta", "org.gamma"]
    assert install.call_count == 3
    per_plugin = [e for e in events if e["stage"] == "plugin_install"]
    assert [(e["status"], e["plugin_index"], e["plugin_total"]) for e in per_plugin] == [
        ("RUNNING", 1, 3), ("OK", 1, 3),
        ("RUNNING", 2, 3), ("OK", 2, 3),
        ("RUNNING", 3, 3), ("OK", 3, 3),
    ]
    assert len({row["transaction_ref"] for row in events}) == 1
    assert "plugin=1/3" in output.getvalue()
    assert "plugin=2/3" in output.getvalue()
    assert "plugin=3/3" in output.getvalue()
    assert events[-1]["outcome"] == "success"
    assert "percent" not in repr(events)
    assert "alpha.zip" not in output.getvalue()


def test_second_plugin_failure_marks_two_of_three_as_attempted_not_success(
    tmp_path, monkeypatch,
):
    snapshot = _snapshot(tmp_path, monkeypatch)
    calls = []
    restore = Mock()
    def fail_second(archive, *, activate):
        calls.append(archive.stem)
        if len(calls) == 2:
            raise dev_updater.DevUpdateError(SECRET)
    monkeypatch.setattr(dev_updater, "_install_plugin_replace", fail_second)
    monkeypatch.setattr(dev_updater, "_restore_plugins", restore)

    events, output = [], io.StringIO()
    with pytest.raises(dev_updater.DevUpdateError):
        with update_operation("dev_snapshot", callback=events.append, stream=output):
            dev_updater.apply_snapshot(snapshot)

    assert calls == ["alpha", "beta"]
    restore.assert_called_once()
    per_plugin = [e for e in events if e["stage"] == "plugin_install"]
    assert [(e["status"], e["plugin_index"], e["plugin_total"]) for e in per_plugin] == [
        ("RUNNING", 1, 3), ("OK", 1, 3),
        ("RUNNING", 2, 3), ("ERROR", 2, 3),
    ]
    assert per_plugin[-1]["reason_code"] == "update_error"
    assert not any(e["plugin_index"] == 3 for e in per_plugin)
    assert [e["status"] for e in events if e["stage"] == "rollback"] == [
        "ROLLBACK", "OK",
    ]
    assert events[-1]["outcome"] == "failed"
    assert SECRET not in output.getvalue() + repr(events)
    assert "plugin=2/3" in output.getvalue()
    assert "plugin=3/3" not in output.getvalue()


@pytest.mark.parametrize(("ordinal", "total"), [
    (0, 3), (4, 3), (-1, 3), (1, 0), (1, 2049),
    (True, 3), (1, True), ("1", 3), (1, "3"),
    (1, None), (None, 3), (SECRET, SECRET),
])
def test_untrusted_ordinals_are_suppressed_before_callback_and_stream(
    ordinal, total,
):
    events, output = [], io.StringIO()
    progress = UpdateProgress("dev_snapshot", callback=events.append, stream=output)
    progress.emit(
        "plugin_install", "RUNNING",
        plugin_index=ordinal, plugin_total=total,
    )
    assert "plugin_index" not in events[-1]
    assert "plugin_total" not in events[-1]
    assert "plugin=" not in output.getvalue()
    assert SECRET not in output.getvalue() + repr(events)


def test_ordinals_are_never_attached_to_unrelated_stage_or_disabled_capture():
    events, output = [], io.StringIO()
    with update_operation("archive", callback=events.append, stream=output):
        with update_phase("smoke", plugin_index=1, plugin_total=2):
            pass
        with update_phase("plugin_install"):
            pass
    assert all("plugin_index" not in event for event in events)
    assert "plugin=" not in output.getvalue()

    # An unobserved library API call needs no log or mutation to proceed.
    with update_phase("plugin_install", plugin_index=2, plugin_total=3):
        assert 2 + 3 == 5
