"""P0 #1143: external cover URLs, response headers and HTTP exceptions never enter logs.

The worker uses httpx.MockTransport, and the cache is inside pytest's temp tree.
No provider access, image decoder or actual HTTP request is involved.
"""
from __future__ import annotations

import json
import logging
import threading

import httpx
import pytest

from cifereca.diagnostics import Diagnostics
from cifereca.gui.cover_loader import CoverLoader, _cover_failure_code


SECRET = "TOKEN_PRIVATE_COVER"
URL = "https://private-host.invalid/LOAN/identity/private-cover.png?token=" + SECRET
VALID_IMAGE = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


@pytest.mark.parametrize(("failure", "reason"), [
    ("http_status", "http_status"),
    ("timeout", "timeout"),
    ("transport", "request_failed"),
    ("invalid_image", "invalid_image"),
    ("unexpected", "unexpected"),
])
def test_remote_cover_failure_diagnostics_are_typed_and_never_export_private_data(
    tmp_path, monkeypatch, caplog, failure, reason,
):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    diagnostics = Diagnostics(level="detailed")
    loader = CoverLoader(diagnostics=diagnostics, max_workers=2)
    original = loader.client

    def handler(request):
        assert str(request.url) == URL
        if failure == "http_status":
            return httpx.Response(403, content=SECRET.encode(), request=request)
        if failure == "timeout":
            raise httpx.ReadTimeout(SECRET, request=request)
        if failure == "transport":
            raise httpx.ConnectError(SECRET, request=request)
        if failure == "invalid_image":
            return httpx.Response(200, content=b"<html>" + SECRET.encode(),
                                  headers={"content-type": "text/html; token=" + SECRET},
                                  request=request)
        raise RuntimeError(SECRET)

    loader.client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    original.close()
    seen = []
    done = threading.Event()
    try:
        with caplog.at_level(logging.DEBUG, logger="cifereca"):
            loader.load(URL, lambda result: (seen.append(result), done.set()))
            assert done.wait(3.0)
            loader._queue.join()
        assert seen == [None]
        rows = diagnostics.snapshot()
        errors = [row for row in rows if row["operation"] == "download"]
        assert len(errors) == 1
        assert errors[0]["detail"] == {"code": reason}
        assert errors[0]["status"] == "error"
        assert errors[0]["duration_ms"] >= 0
        assert loader.stats()["download_failed"] == 1
        assert f"TRACE cover download failed reason={reason}" in caplog.text
        exported = json.dumps(rows) + diagnostics.text("test") + caplog.text
        for private in (SECRET, "private-host.invalid", "/LOAN/", "text/html;"):
            assert private not in exported
    finally:
        loader.close()


def test_successful_cover_trace_does_not_log_private_url_or_provider_mime(
    tmp_path, monkeypatch, caplog,
):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    diagnostics = Diagnostics(level="detailed")
    loader = CoverLoader(diagnostics=diagnostics, max_workers=2)
    original = loader.client

    def handler(request):
        return httpx.Response(200, content=VALID_IMAGE,
                              headers={"content-type": "image/png; token=" + SECRET},
                              request=request)

    loader.client = httpx.Client(transport=httpx.MockTransport(handler))
    original.close()
    got, done = [], threading.Event()
    try:
        with caplog.at_level(logging.DEBUG, logger="cifereca"):
            loader.load(URL, lambda result: (got.append(result), done.set()))
            assert done.wait(3.0)
            loader._queue.join()
        assert len(got) == 1 and got[0] is not None
        assert got[0].read_bytes() == VALID_IMAGE
        assert loader.stats()["download_ok"] == 1
        assert "TRACE cover download ok bytes=" in caplog.text
        for private in (SECRET, "private-host.invalid", "/LOAN/", "image/png;"):
            assert private not in caplog.text + diagnostics.text("test")
    finally:
        loader.close()


def test_cover_failure_classification_does_not_read_exception_text():
    class HostileError(RuntimeError):
        def __str__(self):
            raise AssertionError("Must not stringify raw provider errors")

        def __repr__(self):
            raise AssertionError("Must not repr raw provider errors")

    assert _cover_failure_code(HostileError()) == "unexpected"
    assert _cover_failure_code(OSError(SECRET)) == "storage_failed"
    assert _cover_failure_code(ValueError(SECRET)) == "invalid_image"
    assert _cover_failure_code(httpx.ConnectError(SECRET)) == "request_failed"
    assert _cover_failure_code(httpx.TimeoutException(SECRET)) == "timeout"
