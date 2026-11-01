"""#4093: startup failures export one private ZIP without leaking error text."""
from __future__ import annotations

import json
import sys
import zipfile

import pytest

from cifereca import app, http_trace
from cifereca.http_trace import HTTPTraceCapture


@pytest.mark.parametrize("stage", ["core_open", "gtk_import", "plugin_activation"])
def test_capture_failure_is_closed_and_archive_remains_available(
    monkeypatch, tmp_path, capsys, stage,
):
    capture_argv = ["cifereca", "--capture", "--capture-dir", str(tmp_path)]
    monkeypatch.setattr(sys, "argv", capture_argv)
    monkeypatch.setattr(app, "build_identity", lambda: {"status": "unavailable"})
    monkeypatch.setattr(http_trace, "HTTP_TRACE", HTTPTraceCapture())

    def broken_gui(*, capture_mode=False):
        assert capture_mode is True
        raise RuntimeError("PRIVATE_SIGNED_URL_SYNTHETIC_" + stage)

    monkeypatch.setattr(app, "_run_application", broken_gui)
    assert app.main() == 1
    assert sys.argv is capture_argv
    archives = list(tmp_path.glob("cifereca-capture-*.zip"))
    assert len(archives) == 1
    with zipfile.ZipFile(archives[0]) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        log = archive.read("logs/journal.jsonl").decode("utf-8")
        assert manifest["app_exit_code"] == 1
        assert "capture_runtime_failed" in log
        assert "PRIVATE_SIGNED_URL_SYNTHETIC" not in log
    output = capsys.readouterr().err
    assert "capture_zip:" in output
    assert "PRIVATE_SIGNED_URL_SYNTHETIC" not in output
