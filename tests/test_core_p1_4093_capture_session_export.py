"""P1 #4093: private combined capture ZIP and real GUI entry are offline-testable."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import logging
import os
import sys
import zipfile

import pytest

from cifereca import app
from cifereca.core.capture_session_export import (
    export_capture_session, validate_capture_session,
)


def _journal(raw=b"synthetic startup\nsynthetic shutdown\n", coverage="complete"):
    return {
        "schema_version": 1,
        "format": "cifereca-capture-session-journal",
        "session_ref": "c" * 32, "process_id": 1234,
        "started_at_utc": "2026-11-01T10:00:00+00:00",
        "finished_at_utc": "2026-11-01T10:01:00+00:00",
        "coverage": coverage, "omitted_records": int(coverage == "partial"),
        "record_count": 2, "raw": raw,
    }


def _body(raw, state="complete", representation="transport_bytes"):
    return {"state": state, "observed_bytes": len(raw),
            "representation": representation, "data": bytearray(raw)}


def _entry(ref, response_body, response_headers, *,
           representation="transport_bytes", state="complete"):
    return {
        "exchange_ref": ref, "started_at": "2026-11-01T10:00:10+00:00",
        "request": {
            "method": "GET", "url": "https://invalid.example/private?key=PRIVATE_SIGNED",
            "headers": [["cookie", "PRIVATE_COOKIE"]], "producer": "synthetic",
        },
        "response": {"status_code": 200, "headers": response_headers},
        "outcome": "complete", "duration_ms": 12,
        "request_body": _body(b""),
        "response_body": _body(response_body, state=state,
                               representation=representation),
    }


def _http(exchanges, omitted=0):
    return {
        "schema": "cifereca.http-trace/1", "session_ref": "h" * 32,
        "representation": "transport_bytes_before_response_decompression",
        "omitted_exchanges": omitted, "used_bytes": 100,
        "limits": {"body_bytes": 2097152, "session_bytes": 33554432,
                   "exchanges": 1000, "metadata_bytes": 65536},
        "exchanges": exchanges,
    }


def test_combined_zip_has_session_only_log_raw_http_and_html_utf8(tmp_path):
    import gzip
    html = "<html><h1>Réussi PRIVATE_HTML</h1></html>".encode("latin-1")
    compressed = gzip.compress(html)
    exchanges = [
        _entry("a" * 32, compressed, [
            ["Content-Type", "text/html; charset=ISO-8859-1"],
            ["Content-Encoding", "gzip"],
        ]),
        _entry("b" * 32, b'{"private":true}', [
            ["content-type", "application/json"],
        ]),
        _entry("d" * 32, b"<html>not read</html>", [
            ["content-type", "text/html"],
        ], state="not_read"),
    ]
    trace = _http(exchanges)
    expected_log = b"PRIVATE_SESSION_ONLY\nPRIVATE_SHUTDOWN\n"
    result = export_capture_session(
        tmp_path, journal=_journal(expected_log),
        http_snapshot=trace, app_exit_code=0,
        build_identity={"status": "unavailable"},
    )
    assert result.parent == tmp_path
    assert result.name.startswith("cifereca-capture-")
    assert result.suffix == ".zip"
    assert os.stat(result).st_mode & 0o777 == 0o600
    with zipfile.ZipFile(result) as archive:
        names = archive.namelist()
        manifest = json.loads(archive.read("manifest.json"))
        trace_index = json.loads(archive.read("http/index.json"))
        assert archive.read("logs/journal.jsonl") == expected_log
        assert archive.read("http/bodies/" + "a" * 32 + "-response.bin") == compressed
        assert archive.read("html/" + "a" * 32 + ".html") == html.decode("latin-1").encode("utf-8")
        assert "html/" + "b" * 32 + ".html" not in names
        assert "html/" + "d" * 32 + ".html" not in names
        assert manifest["html_projected"] == 1
        assert manifest["journal_coverage"] == "complete"
        assert manifest["http_coverage"] == "partial"
        assert manifest["coverage"] == "partial"
        assert manifest["app_exit_code"] == 0
        assert trace_index["exchanges"][0]["request"]["headers"][0][1] == "PRIVATE_COOKIE"
        assert trace_index["exchanges"][0]["response_body"]["stored_bytes"] == len(compressed)
        assert trace_index["html_projections"] == [
            {"exchange_ref": "a" * 32, "member": "html/" + "a" * 32 + ".html"},
        ]
    assert validate_capture_session(result)["status"] == "valid"
    assert trace["exchanges"][0]["response_body"]["data"] == compressed


def test_unobserved_or_badly_encoded_html_never_invented(tmp_path):
    trace = _http([
        _entry("a" * 32, b"not gzip", [
            ["content-type", "text/html"], ["content-encoding", "gzip"],
        ]),
        _entry("b" * 32, b"\xff\xfe", [
            ["content-type", "text/html; charset=utf-8"],
        ]),
        _entry("c" * 32, b"maybe", [
            ["content-type", "application/octet-stream"],
        ]),
    ], omitted=2)
    archive_path = export_capture_session(
        tmp_path, journal=_journal(coverage="partial"),
        http_snapshot=trace, app_exit_code=130, trace_active_at_finish=False,
    )
    with zipfile.ZipFile(archive_path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["html_projected"] == 0
        assert manifest["coverage"] == "partial"
        assert manifest["http_omitted_exchanges"] == 2
        assert not any(n.startswith("html/") for n in archive.namelist())
    assert validate_capture_session(archive_path)["status"] == "valid"


def test_invalid_destination_is_not_replaced_or_followed(tmp_path):
    target = tmp_path / "private"
    target.mkdir()
    symlink = tmp_path / "alias"
    symlink.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="capture_directory_unavailable"):
        export_capture_session(symlink, journal=_journal(),
                               http_snapshot=_http([]), app_exit_code=0)
    with pytest.raises(ValueError, match="capture_directory_unavailable"):
        export_capture_session(tmp_path / "absent", journal=_journal(),
                               http_snapshot=_http([]), app_exit_code=0)
    assert not list(target.glob("*.zip"))


def test_capture_export_hash_integrity_detects_tampered_member(tmp_path):
    result = export_capture_session(
        tmp_path, journal=_journal(), http_snapshot=_http([]), app_exit_code=0,
    )
    with zipfile.ZipFile(result) as archive:
        content = {name: archive.read(name) for name in archive.namelist()}
    content["logs/journal.jsonl"] = b"PRIVATE_TAMPERED"
    damaged = tmp_path / "changed.zip"
    with zipfile.ZipFile(damaged, "w") as archive:
        for name, payload in content.items():
            archive.writestr(name, payload)
    with pytest.raises(ValueError, match="capture_archive_invalid"):
        validate_capture_session(damaged)


def test_gui_entry_creates_zip_without_real_gtk(monkeypatch, tmp_path, capsys):
    original_argv = sys.argv
    monkeypatch.setattr(sys, "argv", ["cifereca", "--capture", "--capture-dir",
                                      str(tmp_path), "--no-plugins"])
    monkeypatch.setattr(app, "build_identity", lambda: {"status": "unavailable"})
    called = []
    def fake_app(*, capture_mode=False):
        called.append((capture_mode, sys.argv[:]))
        logging.getLogger("cifereca").error("PRIVATE_SYNTHETIC_STARTUP")
        logging.getLogger("cifereca").error("PRIVATE_SYNTHETIC_SHUTDOWN")
        return 0
    monkeypatch.setattr(app, "_run_application", fake_app)
    from cifereca.http_trace import HTTP_TRACE
    monkeypatch.setattr(HTTP_TRACE, "snapshot", lambda: _http([]))
    assert app.main() == 0
    assert called == [(True, ["cifereca", "--no-plugins"])]
    archive_path, = tmp_path.glob("cifereca-capture-*.zip")
    with zipfile.ZipFile(archive_path) as archive:
        log = archive.read("logs/journal.jsonl")
    assert b"PRIVATE_SYNTHETIC_STARTUP" in log
    assert b"PRIVATE_SYNTHETIC_SHUTDOWN" in log
    err = capsys.readouterr().err
    assert "capture_zip:" in err
    assert "PRIVATE_SYNTHETIC" not in err
    monkeypatch.setattr(sys, "argv", original_argv)


def test_trace_only_keeps_existing_semantics_no_auto_export(
    monkeypatch, tmp_path,
):
    monkeypatch.setattr(sys, "argv", ["cifereca", "--trace"])
    monkeypatch.setattr(app, "_run_application", lambda **kw: 0)
    assert app.main() == 0
    assert not list(tmp_path.glob("*.zip"))


def test_capture_dir_requires_opt_in_and_valid_directory(
    monkeypatch, tmp_path, capsys,
):
    monkeypatch.setattr(sys, "argv", ["cifereca", "--capture-dir", str(tmp_path)])
    assert app.main() == 2
    assert "capture_directory_requires_capture" in capsys.readouterr().err
    monkeypatch.setattr(sys, "argv", ["cifereca", "--capture",
                                     "--capture-dir", str(tmp_path / "missing")])
    assert app.main() == 2
    assert "capture_directory_unavailable" in capsys.readouterr().err
