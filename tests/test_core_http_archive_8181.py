"""Actual HTTP snapshot consumer of the common encrypted vault."""
from io import BytesIO
import json
from zipfile import ZipFile

import httpx
import pytest

from cifereca import http_trace
from cifereca.core.capture_vault_service import CoreCaptureVault
from cifereca.plugin_api.capture_vault import CaptureScope, VaultError


def test_http_archive_consent_restart_and_current_binary_policy(tmp_path, monkeypatch):
    capture = http_trace.HTTPTraceCapture()
    monkeypatch.setattr(http_trace, "HTTP_TRACE", capture)
    monkeypatch.setattr(http_trace, "_trace_active", lambda: True)
    def handler(req):
        if req.url.path == "/image":
            return httpx.Response(200, headers={"content-type": "image/png"},
                                  content=b"\x89PNG\r\n\x1a\n" + b"fictional" * 100)
        return httpx.Response(200, headers={"content-type": "application/json"},
                              content=b'{"fixture":true}')
    with httpx.Client(transport=http_trace.TraceHTTPTransport(
            transport=httpx.MockTransport(handler), capture=capture)) as client:
        client.get("https://fixture.invalid/image")
        client.get("https://fixture.invalid/data")
    def open_core():
        return CoreCaptureVault(tmp_path / "vault", tmp_path / "keys",
                                authorize_scope=lambda *_: True)
    core = open_core()
    port = core.for_plugin("ui.page.diagnostics")
    try:
        with pytest.raises(VaultError, match="vault_confirmation_required"):
            port.archive_http_trace()
        assert not (tmp_path / "vault").exists()
        cid = port.archive_http_trace(consented=True)
        assert not list(tmp_path.rglob("*.zip"))
        assert core.for_plugin("ui.page.browser-recorder").sessions(CaptureScope()) == ()
    finally:
        core.close()
    core = open_core()
    port = core.for_plugin("ui.page.diagnostics")
    try:
        capture.set_include_binary(False)
        destination = tmp_path / "confirmed.zip"
        with pytest.raises(VaultError, match="vault_confirmation_required"):
            port.export_http_archive(cid, str(destination))
        assert not destination.exists()
        port.export_http_archive(cid, str(destination), confirmed=True)
        assert destination.stat().st_mode & 0o777 == 0o600
        with ZipFile(destination) as archive:
            index = json.loads(archive.read("index.json"))
            first, second = index["exchanges"]
            assert first["response_body"]["state"] == "binary_excluded"
            assert "blob" not in first["response_body"]
            assert archive.read(second["response_body"]["blob"]) == b'{"fixture":true}'
    finally:
        core.close()
