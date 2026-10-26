"""P0 #3204: installer phase evidence and process-tree safety (offline)."""
from __future__ import annotations

import io
import json
import os
import sys
import time
from pathlib import Path

import pytest

from cifereca import dev_updater, update_child
from cifereca.update_progress import (
    UpdateProgress, child_progress_receiver, update_operation, update_phase,
)

PRIVATE = "https://borrower.invalid/?token=PRIVATE_LOAN"


def _cli(tmp_path: Path, source: str) -> Path:
    script = tmp_path / "synthetic-plugin-cli"
    script.write_text("#!" + sys.executable + "\n" + source, encoding="utf-8")
    script.chmod(0o700)
    return script


def test_installer_child_streams_actual_subphase_with_verified_package(tmp_path, monkeypatch):
    script = _cli(tmp_path, """
import json, os, sys, time
assert os.environ["CIFERECA_UPDATE_PROGRESS_JSONL"] == "1"
for phase in ("plugin_inspect", "plugin_unpack", "plugin_dependencies", "plugin_enable"):
    print(json.dumps({"schema": "cifereca.update.progress/1",
                      "stage": phase, "status": "RUNNING",
                      "method": "forged", "transaction_ref": "forged",
                      "package_id": "private.other"}), file=sys.stderr, flush=True)
    time.sleep(.02)
print('{"ok": true}')
""")
    monkeypatch.setattr(dev_updater, "_new_cli", lambda: script)
    events, stream = [], io.StringIO()
    with update_operation("dev_snapshot", callback=events.append, stream=stream):
        with update_phase("plugin_install", plugin_index=1, plugin_total=21,
                          package_id="org.cifereca.test", package_version="1.2.3"):
            dev_updater._install_plugin_replace(tmp_path / "synthetic.zip", activate=True)
    stages = [e["stage"] for e in events if e["stage"].startswith("plugin_")]
    assert stages == [
        "plugin_install", "plugin_inspect", "plugin_unpack",
        "plugin_dependencies", "plugin_enable", "plugin_install",
    ]
    assert [e["status"] for e in events if e["stage"] == "plugin_install"] == [
        "RUNNING", "OK",
    ]
    assert all(e["method"] == "dev_snapshot" for e in events)
    assert len({e["transaction_ref"] for e in events}) == 1
    assert all("package_id" not in e for e in events if e["stage"] != "plugin_install")
    assert events[1]["package_id"] == "org.cifereca.test"
    assert events[1]["package_version"] == "1.2.3"
    assert "package=org.cifereca.test@1.2.3" in stream.getvalue()
    assert "private.other" not in repr(events) + stream.getvalue()


def test_long_progressing_cli_exceeds_old_small_budget_without_false_timeout(tmp_path, monkeypatch):
    script = _cli(tmp_path, """
import json, sys, time
for stage in ("plugin_unpack", "plugin_dependencies", "plugin_enable"):
    print(json.dumps({"schema": "cifereca.update.progress/1",
                      "stage": stage, "status": "RUNNING"}), file=sys.stderr, flush=True)
    time.sleep(.12)
print('{"ok": true}')
""")
    monkeypatch.setattr(dev_updater, "_new_cli", lambda: script)
    # Scaled budgets keep this test fast while exercising a live progressing
    # install which would exceed a smaller fixed, stage-blind deadline.
    monkeypatch.setattr(dev_updater, "PLUGIN_INSTALL_TIMEOUT_SECONDS", 1.0)
    dev_updater._install_plugin_replace(tmp_path / "synthetic.zip", activate=True)


@pytest.mark.skipif(os.name != "posix", reason="process group verification on POSIX")
def test_timeout_reaps_pip_like_descendant_before_rollback(tmp_path, monkeypatch):
    marker = tmp_path / "orphan-would-write"
    script = _cli(tmp_path, f"""
import subprocess, sys, time
subprocess.Popen([sys.executable, "-c",
    "import pathlib,time;time.sleep(.8);pathlib.Path({str(marker)!r}).write_text('orphan')"])
time.sleep(10)
""")
    monkeypatch.setattr(dev_updater, "_new_cli", lambda: script)
    monkeypatch.setattr(dev_updater, "PLUGIN_INSTALL_TIMEOUT_SECONDS", .3)
    with pytest.raises(TimeoutError, match="Délai installation plugin dépassé"):
        dev_updater._install_plugin_replace(tmp_path / "synthetic.zip", activate=True)
    # The descendant must not outlive the caller and mutate paths after the
    # updater has started its transactional restoration.
    time.sleep(.85)
    assert not marker.exists()


def test_untrusted_package_identity_never_crosses_child_boundary():
    events = []
    with update_operation("github", callback=events.append):
        child_progress_receiver()(json.dumps({
            "schema": "cifereca.update.progress/1",
            "stage": "plugin_install", "status": "RUNNING",
            "plugin_index": 1, "plugin_total": 2,
            "package_id": PRIVATE, "package_version": PRIVATE,
        }).encode())
    assert any(e.get("plugin_index") == 1 for e in events)
    assert all("package_id" not in e for e in events)
    assert PRIVATE not in repr(events)


@pytest.mark.parametrize("package,version", [
    (PRIVATE, "1.0.0"), ("org.test", PRIVATE), ("org.test", "1.0.0\nINJECT"),
    ("org.test", None), ("", "1.0.0"),
])
def test_invalid_package_labels_suppressed(package, version):
    events, log = [], io.StringIO()
    progress = UpdateProgress("dev_snapshot", callback=events.append, stream=log)
    progress.emit("plugin_install", "RUNNING", plugin_index=1, plugin_total=3,
                  package_id=package, package_version=version)
    assert "package_version" not in events[-1]
    assert PRIVATE not in log.getvalue()
