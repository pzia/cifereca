"""P1 #4093: GUI --capture + actual HTTPX Trace wrapper, fully offline.

Real transport code runs against a deterministic httpx.MockTransport, not a
provider or external internet. This verifies that one session ZIP really
contains the observed redirect, raw gzip response, decoded HTML and log.
"""
from __future__ import annotations

import gzip
import json
import logging
import sys
import zipfile

import httpx

from cifereca import app, http_trace
from cifereca.core.capture_session_export import validate_capture_session
from cifereca.http_trace import HTTPTraceCapture, TraceHTTPTransport


def test_capture_gui_session_observes_only_real_mock_httpx_requests_and_html(
    monkeypatch, tmp_path, capsys,
):
    monkeypatch.setattr(sys, "argv", [
        "cifereca", "--capture", "--capture-dir", str(tmp_path),
    ])
    monkeypatch.setattr(app, "build_identity", lambda: {"status": "unavailable"})
    # This process-local buffer represents exactly the newly started GUI.
    fresh = HTTPTraceCapture()
    monkeypatch.setattr(http_trace, "HTTP_TRACE", fresh)
    requests: list[tuple[str, str]] = []
    html = "<html><body>PRIVATE_SYNTHETIC_LIVRE é</body></html>"
    compressed = gzip.compress(html.encode("utf-8"))

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path))
        if request.url.path == "/catalog":
            return httpx.Response(302, headers={"Location": "/book"})
        assert request.url.path == "/book"
        return httpx.Response(
            200,
            headers={
                "Content-Type": "text/html; charset=utf-8",
                "Content-Encoding": "gzip",
            },
            content=compressed,
        )

    def simulate_gui(*, capture_mode=False):
        assert capture_mode is True
        logging.getLogger("cifereca").error("PRIVATE_SYNTHETIC_STARTUP")
        with httpx.Client(
            transport=TraceHTTPTransport(
                transport=httpx.MockTransport(respond), producer="synthetic-offline",
            ),
            base_url="https://example.invalid", follow_redirects=True,
        ) as client:
            response = client.get("/catalog", headers={"X-Private": "PRIVATE_KEY"})
            assert response.status_code == 200
            assert response.text == html
        logging.getLogger("cifereca").error("PRIVATE_SYNTHETIC_SHUTDOWN")
        return 0

    monkeypatch.setattr(app, "_run_application", simulate_gui)
    assert app.main() == 0
    # Exporting must not replay any GET and redirects are independent entries.
    assert requests == [("GET", "/catalog"), ("GET", "/book")]
    archive_path, = list(tmp_path.glob("cifereca-capture-*.zip"))
    assert validate_capture_session(archive_path)["status"] == "valid"
    with zipfile.ZipFile(archive_path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        index = json.loads(archive.read("http/index.json"))
        log = archive.read("logs/journal.jsonl")
        assert b"PRIVATE_SYNTHETIC_STARTUP" in log
        assert b"PRIVATE_SYNTHETIC_SHUTDOWN" in log
        assert len(index["exchanges"]) == 2
        assert manifest["http_exchanges"] == 2
        first, second = index["exchanges"]
        assert first["response"]["status_code"] == 302
        assert second["response"]["status_code"] == 200
        assert second["request"]["headers"]
        assert b"PRIVATE_KEY" in archive.read("http/index.json")
        ref = second["exchange_ref"]
        assert archive.read(f"http/bodies/{ref}-response.bin") == compressed
        assert archive.read(f"html/{ref}.html") == html.encode("utf-8")
        assert index["html_projections"] == [{
            "exchange_ref": ref, "member": f"html/{ref}.html",
        }]
        assert manifest["html_projected"] == 1
    errors = capsys.readouterr().err
    assert "capture_zip:" in errors
    assert "PRIVATE_KEY" not in errors
    assert "PRIVATE_SYNTHETIC_LIVRE" not in errors
    assert requests == [("GET", "/catalog"), ("GET", "/book")]
