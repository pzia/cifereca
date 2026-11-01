"""P1 #4093: UTF-8 HTML projection stays within its actual on-disk budget.

The transport blob is never altered. Synthetic repeat text only, no network.
"""
from __future__ import annotations

import json
import zipfile

import pytest

from cifereca.core.capture_session_export import (
    _MAX_HTML_BYTES, export_capture_session, validate_capture_session,
)
from tests.test_core_p1_4093_capture_session_export import _entry, _http, _journal


@pytest.mark.parametrize("repeated", [1_100_000, 1_500_000])
def test_latin1_to_utf8_growth_exceeding_cap_omits_only_html_projection(
    tmp_path, repeated,
):
    encoded_source = b"\xe9" * repeated
    assert len(encoded_source) < _MAX_HTML_BYTES
    assert len(encoded_source.decode("latin-1").encode("utf-8")) > _MAX_HTML_BYTES
    entry = _entry("a" * 32, encoded_source, [
        ["content-type", "text/html; charset=iso-8859-1"],
    ])
    result = export_capture_session(
        tmp_path, journal=_journal(), http_snapshot=_http([entry]), app_exit_code=0,
    )
    with zipfile.ZipFile(result) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        index = json.loads(archive.read("http/index.json"))
        assert archive.read("http/bodies/" + "a" * 32 + "-response.bin") == encoded_source
        assert "html/" + "a" * 32 + ".html" not in archive.namelist()
        assert manifest["html_projected"] == 0
        assert manifest["html_unavailable"] == 1
        assert manifest["coverage"] == "partial"
        assert index["html_unavailable"] == [
            {"exchange_ref": "a" * 32, "reason": "encoding_unavailable"},
        ]
    assert validate_capture_session(result)["status"] == "valid"


def test_small_latin1_projection_still_produces_full_unicode_html(tmp_path):
    body = b"\xe9" * 512_000
    encoded = body.decode("latin-1").encode("utf-8")
    assert len(encoded) <= _MAX_HTML_BYTES
    entry = _entry("b" * 32, body, [
        ["content-type", "text/html; charset=iso-8859-1"],
    ])
    result = export_capture_session(
        tmp_path, journal=_journal(), http_snapshot=_http([entry]), app_exit_code=0,
    )
    with zipfile.ZipFile(result) as archive:
        assert archive.read("html/" + "b" * 32 + ".html") == encoded
        assert archive.read("http/bodies/" + "b" * 32 + "-response.bin") == body
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["html_projected"] == 1
        assert manifest["html_unavailable"] == 0
    assert validate_capture_session(result)["status"] == "valid"
