"""#4093: Trace OFF then ON must never claim complete HTTP session coverage."""
from __future__ import annotations

import json
import logging
import sys
import zipfile

import pytest

from cifereca import app
from cifereca.core.capture_session_export import export_capture_session
from cifereca.logging_utils import (
    diagnostics_level, set_diagnostics_level, trace_disable_generation,
)


def _journal():
    return {
        "schema_version": 1,
        "format": "cifereca-capture-session-journal",
        "session_ref": "a" * 32, "process_id": 1,
        "started_at_utc": "2026-11-01T15:00:00+00:00",
        "finished_at_utc": "2026-11-01T15:01:00+00:00",
        "coverage": "complete", "omitted_records": 0,
        "record_count": 0, "raw": b"",
    }


def _http():
    return {
        "schema": "cifereca.http-trace/1",
        "session_ref": "b" * 32,
        "omitted_exchanges": 0, "exchanges": [],
    }


def test_disable_generation_changes_only_on_real_trace_exit():
    previous = diagnostics_level()
    try:
        set_diagnostics_level("trace")
        start = trace_disable_generation()
        set_diagnostics_level("trace")
        assert trace_disable_generation() == start
        set_diagnostics_level("normal")
        assert trace_disable_generation() == start + 1
        set_diagnostics_level("errors")
        assert trace_disable_generation() == start + 1
        set_diagnostics_level("trace")
        assert trace_disable_generation() == start + 1
        with pytest.raises(ValueError):
            set_diagnostics_level("PRIVATE_BAD_LEVEL")
        assert trace_disable_generation() == start + 1
    finally:
        set_diagnostics_level(previous)


@pytest.mark.parametrize("discontinuous", [False, True])
def test_capture_cli_marks_off_then_on_as_partial_even_when_trace_active_at_exit(
    monkeypatch, tmp_path, capsys, discontinuous,
):
    previous = diagnostics_level()
    monkeypatch.setattr(sys, "argv", [
        "cifereca", "--capture", "--capture-dir", str(tmp_path),
    ])
    monkeypatch.setattr(app, "build_identity", lambda: {"status": "unavailable"})
    from cifereca.http_trace import HTTP_TRACE
    monkeypatch.setattr(HTTP_TRACE, "snapshot", _http)

    def fake_run(*, capture_mode=False):
        assert capture_mode is True
        assert diagnostics_level() == "trace"
        logging.getLogger("cifereca").error("SYNTHETIC_SESSION_EVENT")
        if discontinuous:
            set_diagnostics_level("normal")
            set_diagnostics_level("trace")
        assert diagnostics_level() == "trace"
        return 0

    monkeypatch.setattr(app, "_run_application", fake_run)
    try:
        assert app.main() == 0
        output, = list(tmp_path.glob("cifereca-capture-*.zip"))
        with zipfile.ZipFile(output) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            assert b"SYNTHETIC_SESSION_EVENT" in archive.read("logs/journal.jsonl")
        assert manifest["trace_interrupted"] is discontinuous
        assert manifest["http_coverage"] == (
            "partial" if discontinuous else "instrumented_only"
        )
        assert manifest["coverage"] == (
            "partial" if discontinuous else "instrumented_only"
        )
        assert "capture_zip:" in capsys.readouterr().err
    finally:
        set_diagnostics_level(previous)


def test_explicit_export_flag_does_not_invent_continuous_http_coverage(tmp_path):
    output = export_capture_session(
        tmp_path, journal=_journal(), http_snapshot=_http(), app_exit_code=0,
        trace_active_at_finish=True, trace_uninterrupted=False,
    )
    with zipfile.ZipFile(output) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    assert manifest["trace_interrupted"] is True
    assert manifest["http_coverage"] == "partial"
    assert manifest["coverage"] == "partial"
