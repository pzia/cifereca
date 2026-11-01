"""P1 #4093: --capture must not permanently enable DEBUG in reused Python.

No actual GTK window or remote provider is created.
"""
from __future__ import annotations

import json
import logging
import sys
import zipfile

import pytest

from cifereca import app, http_trace
from cifereca.http_trace import HTTPTraceCapture
from cifereca.logging_utils import diagnostics_level


@pytest.mark.parametrize("failed", [False, True])
def test_capture_restores_exact_previous_python_logger_level(
    monkeypatch, tmp_path, capsys, failed,
):
    logger = logging.getLogger("cifereca")
    initial_level = logger.level
    logger.setLevel(logging.INFO)
    previous_diagnostics = diagnostics_level()
    monkeypatch.setattr(sys, "argv", [
        "cifereca", "--capture", "--capture-dir", str(tmp_path),
    ])
    monkeypatch.setattr(app, "build_identity", lambda: {"status": "unavailable"})
    monkeypatch.setattr(http_trace, "HTTP_TRACE", HTTPTraceCapture())
    observed = []

    def run_gui(*, capture_mode=False):
        assert capture_mode
        observed.append(logger.level)
        if failed:
            raise RuntimeError("PRIVATE_SYNTHETIC_STARTUP_FAILURE")
        return 0

    monkeypatch.setattr(app, "_run_application", run_gui)
    try:
        assert app.main() == (1 if failed else 0)
        assert observed == [logging.DEBUG]
        assert logger.level == logging.INFO
        assert diagnostics_level() == previous_diagnostics
        result, = tmp_path.glob("cifereca-capture-*.zip")
        with zipfile.ZipFile(result) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            assert manifest["app_exit_code"] == (1 if failed else 0)
        assert "PRIVATE_SYNTHETIC_STARTUP_FAILURE" not in capsys.readouterr().err
    finally:
        logger.setLevel(initial_level)
