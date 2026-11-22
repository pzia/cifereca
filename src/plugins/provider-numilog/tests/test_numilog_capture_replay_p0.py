"""#7163: replay a known capture ZIP offline, without leaking recorded content."""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from zipfile import ZipFile
import gzip
import json

import pytest

from cifereca_numilog_capture_replay import analyse_capture

ROOT = Path(__file__).parent
HTML = (ROOT / "fixtures/observed_search-loup-0.html").read_bytes()
URL = ("https://www.numilog.com/Pages/Recherche/ResultatRecherche.aspx"
       "?mode=brSimple&query=loup")


def _entry(letter, data, *, offset=0, duration=100, coding="identity",
           status=200, cookies=False, producer="provider.numilog.public"):
    ref = letter * 32
    headers = [["Accept", "text/html"], ["Accept-Language", "fr-FR,fr;q=0.9"]]
    if cookies:
        headers.append(["Cookie", "PRIVATE_SENTINEL"])
    started = datetime(2026, 11, 20, 21, 34, tzinfo=timezone.utc)
    started += timedelta(milliseconds=offset)
    return {
        "exchange_ref": ref, "started_at": started.isoformat(),
        "duration_ms": duration,
        "request": {"method": "GET", "url": URL, "producer": producer,
                    "headers": headers},
        "request_body": {"state": "complete", "stored_bytes": 0},
        "response": {"status_code": status, "headers": [
            ["Content-Type", "text/html; charset=utf-8"],
            ["Content-Encoding", coding],
        ]},
        "response_body": {
            "state": "complete", "representation": "transport_bytes",
            "blob": f"http/bodies/{ref}-response.bin",
            "stored_bytes": len(data), "sha256": sha256(data).hexdigest(),
        },
        "_raw": data,
    }


def _archive(path, entries):
    records = []
    with ZipFile(path, "w") as zipfile:
        for entry in entries:
            row = {k: v for k, v in entry.items() if k != "_raw"}
            records.append(row)
            zipfile.writestr(
                row["response_body"]["blob"], entry["_raw"])
        zipfile.writestr("http/index.json", json.dumps({
            "schema": "cifereca.http-trace/1", "exchanges": records,
        }))
    return path


def test_20_november_shape_reports_replay_and_exact_get_overlap_without_secrets(tmp_path):
    entries = [
        _entry("a", HTML, offset=0, duration=100),
        _entry("b", b"<form>PRIVATE_HTML</form>", offset=10, duration=90),
        _entry("c", gzip.compress(HTML), offset=20, duration=80, coding="gzip"),
        _entry("d", b"not found", offset=500, duration=20, status=404),
        _entry("e", HTML, offset=30, duration=90, cookies=True),
    ]
    result = analyse_capture(_archive(tmp_path / "private.zip", entries))
    assert result["status"] == "reviewed"
    assert result["counts"]["exchanges"] == 5
    assert result["counts"]["unqualified"] == 1
    assert result["counts"]["eligible_public_get"] == 4
    assert result["counts"]["distinct_exact_public_get"] == 1
    assert result["counts"]["repeated_exact_public_get_observed"] == 3
    assert result["counts"]["overlapping_exact_public_get_observed"] == 2
    assert result["counts"]["parser_qualified"] == 2
    assert result["reasons"]["http_non_200"] == 1
    assert result["reasons"]["layout:unknown_search_layout"] == 1
    public = json.dumps(result)
    assert "PRIVATE" not in public and "loup" not in public
    assert "numilog.com" not in public and str(tmp_path) not in public


def test_invalid_digest_is_refused_without_body_or_link(tmp_path):
    bad = _entry("a", HTML)
    bad["response_body"]["sha256"] = "0" * 64
    result = analyse_capture(_archive(tmp_path / "wrong.zip", [bad]))
    assert result["reasons"] == {"body_digest_mismatch": 1}
    assert result["counts"]["parser_qualified"] if "parser_qualified" in result["counts"] else 0 == 0


def test_brotli_transport_bytes_are_replayed_only_with_decoder(tmp_path):
    brotli = pytest.importorskip("brotli")
    data = brotli.compress(HTML)
    entry = _entry("a", data, coding="br")
    result = analyse_capture(_archive(tmp_path / "brotli.zip", [entry]))
    assert result["counts"]["parser_qualified"] == 1
    assert result["reasons"] == {}


def test_bad_archive_has_closed_error(tmp_path):
    path = tmp_path / "unreadable.zip"
    path.write_bytes(b"PRIVATE_ARCHIVE_CONTENT")
    assert analyse_capture(path) == {"status": "invalid_capture"}
