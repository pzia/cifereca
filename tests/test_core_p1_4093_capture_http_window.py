"""#4093: no previous session's HTTP URL/body/headers in a new capture ZIP."""
from __future__ import annotations

import json
import logging
import sys
import zipfile

import httpx
import pytest

from cifereca import app, http_trace
from cifereca.core.capture_http_window import (
    CaptureHTTPWindowError, capture_http_window,
)
from cifereca.http_trace import HTTPTraceCapture, TraceHTTPTransport


def test_projection_rejects_reset_trace_or_bad_counters():
    initial = {
        "session_ref": "a" * 32, "exchanges": 3,
        "omitted_exchanges": 1, "used_bytes": 50,
    }
    snapshot = {
        "schema": "cifereca.http-trace/1", "session_ref": "a" * 32,
        "exchanges": [{"exchange_ref": str(i)} for i in range(4)],
        "omitted_exchanges": 2, "used_bytes": 70,
        "representation": "transport_bytes_before_response_decompression",
        "limits": {},
    }
    projected = capture_http_window(snapshot, initial)
    assert len(projected["exchanges"]) == 1
    assert projected["used_bytes"] == 20
    assert projected["omitted_exchanges"] == 1
    assert len(snapshot["exchanges"]) == 4  # shared buffer untouched
    with pytest.raises(CaptureHTTPWindowError):
        capture_http_window({**snapshot, "session_ref": "b" * 32}, initial)
    with pytest.raises(CaptureHTTPWindowError):
        capture_http_window({**snapshot, "exchanges": []}, initial)
    with pytest.raises(CaptureHTTPWindowError):
        capture_http_window({**snapshot, "used_bytes": 10}, initial)


def test_second_gui_capture_never_includes_first_run_http_evidence(
    monkeypatch, tmp_path, capsys,
):
    fresh = HTTPTraceCapture()
    monkeypatch.setattr(http_trace, "HTTP_TRACE", fresh)
    monkeypatch.setattr(app, "build_identity", lambda: {"status": "unavailable"})
    for number in (1, 2):
        label = f"PRIVATE_RUN_{number}"

        def handler(request: httpx.Request):
            return httpx.Response(
                200, headers={"content-type": "text/html; charset=utf-8"},
                text=f"<html>{label}</html>",
            )

        def run_gui(*, capture_mode=False):
            assert capture_mode
            logging.getLogger("cifereca").error(f"SESSION_{number}_BEGIN")
            with httpx.Client(
                transport=TraceHTTPTransport(
                    httpx.MockTransport(handler), producer="synthetic",
                ),
                base_url="https://invalid.example",
            ) as client:
                result = client.get(f"/{label}", headers={"X-Secret": label})
                assert result.status_code == 200
                assert label in result.text
            logging.getLogger("cifereca").error(f"SESSION_{number}_END")
            return 0

        monkeypatch.setattr(app, "_run_application", run_gui)
        monkeypatch.setattr(sys, "argv", [
            "cifereca", "--capture", "--capture-dir", str(tmp_path),
        ])
        assert app.main() == 0

    archives = sorted(tmp_path.glob("cifereca-capture-*.zip"))
    assert len(archives) == 2
    with zipfile.ZipFile(archives[-1]) as zip_new:
        last_index = json.loads(zip_new.read("http/index.json"))
        raw = b"".join(zip_new.read(path) for path in zip_new.namelist())
        assert last_index["exchanges"] and len(last_index["exchanges"]) == 1
        assert last_index["omitted_exchanges"] == 0
        assert b"PRIVATE_RUN_2" in raw
        assert b"SESSION_2_BEGIN" in raw and b"SESSION_2_END" in raw
        assert b"PRIVATE_RUN_1" not in raw
        assert b"SESSION_1_BEGIN" not in raw

    # The process-global diagnostics buffer still has both observed exchanges.
    # Isolating an archive must never clear provider/Diagnostics evidence.
    assert len(fresh.snapshot()["exchanges"]) == 2
    assert "PRIVATE_RUN_1" not in capsys.readouterr().err
