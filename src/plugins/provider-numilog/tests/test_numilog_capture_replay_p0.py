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
           status=200, cookies=False, producer="provider.numilog.public", url=URL):
    ref = letter * 32
    headers = [["Accept", "text/html"], ["Accept-Language", "fr-FR,fr;q=0.9"]]
    if cookies:
        headers.append(["Cookie", "PRIVATE_SENTINEL"])
    started = datetime(2026, 11, 20, 21, 34, tzinfo=timezone.utc)
    started += timedelta(milliseconds=offset)
    return {
        "exchange_ref": ref, "started_at": started.isoformat(),
        "duration_ms": duration,
        "request": {"method": "GET", "url": url, "producer": producer,
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
    assert result["counts"].get("parser_qualified", 0) == 0


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


def test_advanced_readable_layout_is_replayed_but_never_counted_as_coalescible(tmp_path):
    # An Ra GET is eligible for offline parsing, but our installed shared
    # public transport deliberately accepts brSimple only. The diagnostic
    # must not promise to coalesce an unsupported advanced request.
    advanced = ("https://www.numilog.com/Pages/Recherche/ResultatRecherche.aspx"
                "?mode=Ra&RATitle=loup")
    entries = [
        _entry("a", HTML, url=URL, offset=0),
        _entry("b", HTML, url=advanced, offset=10),
        _entry("c", HTML, url=advanced, offset=20),
    ]
    result = analyse_capture(_archive(tmp_path / "advanced.zip", entries))
    assert result["status"] == "reviewed"
    assert result["counts"]["exchanges"] == 3
    assert result["counts"]["eligible_public_get"] == 1
    assert result["counts"]["advanced_get_not_coalescible"] == 2
    assert result["counts"]["distinct_exact_public_get"] == 1
    assert result["counts"]["repeated_exact_public_get_observed"] == 0
    assert result["counts"]["overlapping_exact_public_get_observed"] == 0
    assert sum(result["reasons"].values()) + result["counts"]["parser_qualified"] == 3
    output = json.dumps(result)
    assert "RATitle" not in output and "numilog.com" not in output


def test_duplicate_capture_index_or_body_is_never_accepted_as_a_receipt(tmp_path):
    import warnings
    for member in ("http/index.json", "http/bodies/" + "a" * 32 + "-response.bin"):
        archive = _archive(tmp_path / ("duplicate-index.zip" if member.endswith(".json")
                                      else "duplicate-body.zip"), [
            _entry("a", HTML),
        ])
        # ZIP permits ambiguous duplicate members; getinfo would otherwise
        # select the last content and emit a superficially valid verdict.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with ZipFile(archive, "a") as zipfile:
                zipfile.writestr(member, b"AMBIGUOUS_PRIVATE_BODY")
        assert analyse_capture(archive) == {"status": "invalid_capture"}


@pytest.mark.parametrize("private_header", [
    "X-API-Key", "X-Access-Key", "X-Session-ID", "X-Auth-Context",
    "X-Secret-Key", "X-Credential", "X-Signature", "X-SSO-Ticket",
    "X-User-Key", "X-OAuth-Assertion",
])
def test_private_header_alias_is_not_qualified_as_anonymous_get(tmp_path, private_header):
    entry = _entry("a", HTML)
    entry["request"]["headers"].append([private_header, "PRIVATE_SENTINEL"])
    report = analyse_capture(_archive(tmp_path / "private-header.zip", [entry]))
    assert report["status"] == "reviewed"
    assert report["counts"]["unqualified"] == 1
    assert report["counts"].get("eligible_public_get", 0) == 0
    assert report["counts"].get("parser_qualified", 0) == 0
    output = json.dumps(report)
    assert "PRIVATE_SENTINEL" not in output and "numilog.com" not in output


def test_two_safe_public_gets_with_distinct_headers_are_not_coalescible(tmp_path):
    first = _entry("a", HTML, offset=0)
    second = _entry("b", HTML, offset=1)
    first["request"]["headers"].append(["X-Trace-Id", "probe-a"])
    second["request"]["headers"].append(["X-Trace-Id", "probe-b"])
    report = analyse_capture(_archive(tmp_path / "different-headers.zip", [first, second]))
    assert report["counts"]["eligible_public_get"] == 2
    assert report["counts"]["distinct_exact_public_get"] == 2
    assert report["counts"]["repeated_exact_public_get_observed"] == 0
    assert report["counts"]["overlapping_exact_public_get_observed"] == 0
    assert report["counts"]["parser_qualified"] == 2


def test_decoded_capture_body_is_not_classified_as_a_failed_transport_decode(tmp_path):
    entry = _entry("a", HTML, coding="br")
    entry["response_body"]["representation"] = "decoded_bytes"
    report = analyse_capture(_archive(tmp_path / "decoded-body.zip", [entry]))
    assert report["status"] == "reviewed"
    assert report["counts"]["eligible_public_get"] == 1
    assert report["reasons"] == {"body_representation_unverified": 1}
    assert report["counts"].get("parser_qualified", 0) == 0


def test_oversized_zip_member_inventory_is_rejected_before_replay(tmp_path):
    # One valid trace plus many inert members must not induce an unbounded
    # member-name scan or silently qualify the otherwise valid book.
    path = _archive(tmp_path / "too-many-members.zip", [_entry("a", HTML)])
    with ZipFile(path, "a") as archive:
        for index in range(2001):
            archive.writestr(f"extra/{index:04d}", b"")
    assert analyse_capture(path) == {"status": "invalid_capture"}
