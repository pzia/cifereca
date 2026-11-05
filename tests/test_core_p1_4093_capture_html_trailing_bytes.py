"""#4093: don't mislabel partially-decoded HTML as complete evidence.

Synthetic compression fixtures only. Original transport blobs remain raw in ZIP.
"""
from __future__ import annotations

import gzip
import json
import zipfile
import zlib

import pytest

from cifereca.core.capture_session_export import (
    export_capture_session, validate_capture_session,
)
from tests.test_core_p1_4093_capture_session_export import _entry, _http, _journal


@pytest.mark.parametrize("coding,encode", [
    ("gzip", gzip.compress),
    ("deflate", zlib.compress),
])
# Deterministic module-level fixture: gzip's default mtime makes each xdist
# worker collect a different parametrization ID and aborts the full CI suite.
@pytest.mark.parametrize("tail", [
    pytest.param(b"PRIVATE_TRAILING", id="plain-trailing-bytes"),
    pytest.param(gzip.compress(b"<html>SECOND</html>", mtime=0), id="gzip-trailing-member"),
])
def test_compressed_html_with_trailing_bytes_is_not_claimed_complete(
    tmp_path, coding, encode, tail,
):
    original = b"<html>FIRST</html>"
    raw = encode(original) + tail
    record = _entry("a" * 32, raw, [
        ["content-type", "text/html; charset=utf-8"],
        ["content-encoding", coding],
    ])
    archive_path = export_capture_session(
        tmp_path, journal=_journal(), http_snapshot=_http([record]), app_exit_code=0,
    )
    with zipfile.ZipFile(archive_path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        index = json.loads(archive.read("http/index.json"))
        assert archive.read("http/bodies/" + "a" * 32 + "-response.bin") == raw
        assert manifest["html_projected"] == 0
        assert manifest["html_unavailable"] == 1
        assert index["html_unavailable"] == [{
            "exchange_ref": "a" * 32, "reason": "encoding_unavailable",
        }]
        assert index["html_projections"] == []
        assert not any(name.startswith("html/") for name in archive.namelist())
        assert manifest["coverage"] == "partial"
        assert manifest["http_coverage"] == "partial"
    assert validate_capture_session(archive_path)["status"] == "valid"


@pytest.mark.parametrize("coding,encode", [
    ("gzip", gzip.compress),
    ("deflate", zlib.compress),
])
def test_single_complete_compressed_html_still_projects(
    tmp_path, coding, encode,
):
    html = b"<html>PRIVATE_COMPLETE</html>"
    record = _entry("b" * 32, encode(html), [
        ["content-type", "text/html; charset=utf-8"],
        ["content-encoding", coding],
    ])
    archive_path = export_capture_session(
        tmp_path, journal=_journal(), http_snapshot=_http([record]), app_exit_code=0,
    )
    with zipfile.ZipFile(archive_path) as archive:
        assert archive.read("html/" + "b" * 32 + ".html") == html
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["html_projected"] == 1
        assert manifest["html_unavailable"] == 0
    assert validate_capture_session(archive_path)["status"] == "valid"
