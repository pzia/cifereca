"""P0 #2628: official Playwright CLI with a LOCAL rejecting HTTPS proxy.

The Core starts its real version-coupled Chromium installer, but every
outbound download attempt must traverse a synthetic proxy on 127.0.0.1
that replies 502. No mirror override, provider, credential or user profile.
The exact proxy failure classification is intentionally public and redacted.
The CI gate is explicit: this fixture runs only in the dedicated fresh-venv
browser-provisioning workflow, before any real Chromium download.
"""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import sys
from threading import Thread

import pytest

from cifereca.plugins.playwright_browser import inspect_chromium, install_chromium


pytestmark = pytest.mark.skipif(
    os.environ.get("CIFERECA_REAL_CHROMIUM_PROVISION_E2E") != "1",
    reason="requires explicit ephemeral CI first-install environment",
)


def test_official_chromium_install_fails_closed_behind_rejecting_local_proxy(
    monkeypatch, capsys,
):
    assert sys.platform.startswith("linux")
    assert sys.prefix != sys.base_prefix
    assert Path.home().name == "empty-proxy-home"
    assert os.environ.get("PLAYWRIGHT_BROWSERS_PATH") is None
    assert os.environ.get("PLAYWRIGHT_DOWNLOAD_HOST") is None
    assert os.environ.get("PLAYWRIGHT_CHROMIUM_DOWNLOAD_HOST") is None
    cache = Path.home() / ".cache" / "ms-playwright"
    assert not cache.exists()

    requests_seen = []

    class RefusingProxy(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            # Node's Proxy-Authorization and URL must never enter CI output.
            pass

        def _reject(self):
            requests_seen.append(self.command)
            self.send_response(502)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_CONNECT(self):
            self._reject()

        def do_GET(self):
            self._reject()

    proxy = ThreadingHTTPServer(("127.0.0.1", 0), RefusingProxy)
    proxy.daemon_threads = True
    runner = Thread(target=proxy.serve_forever, daemon=True)
    runner.start()
    assert proxy.server_address[0] == "127.0.0.1"

    # A deliberately fictitious credential ensures no proxy URL gets
    # serialized into BrowserState, diagnostics or pytest stdout.
    test_secret = "FAKE_TEST_PROXY_PASSWORD_NEVER_PUBLISH"
    url = f"http://fixture:{test_secret}@127.0.0.1:{proxy.server_port}"
    try:
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
            monkeypatch.setenv(key, url)
        for key in ("ALL_PROXY", "all_proxy", "NO_PROXY", "no_proxy"):
            monkeypatch.delenv(key, raising=False)
        # A short per-mirror connection timeout keeps the test bounded even
        # if Playwright tries the fallback hosts; Core enforces an absolute
        # timeout for the complete process group regardless.
        monkeypatch.setenv("PLAYWRIGHT_DOWNLOAD_CONNECTION_TIMEOUT", "2000")

        assert inspect_chromium().code == "chromium_missing"
        assert install_chromium(consent=False).code == "consent_required"
        assert requests_seen == [] and not cache.exists()

        phases = []
        status = install_chromium(
            consent=True, progress=phases.append, timeout=35,
        )
        assert status.code in {"download_failed", "download_timeout"}, (
            "real CLI unexpectedly installed Chromium through rejecting proxy: "
            + status.code
        )
        assert requests_seen, "official Playwright never contacted local proxy"
        assert set(requests_seen) <= {"CONNECT", "GET"}
        assert phases == ["starting"]
        assert inspect_chromium().code in {
            "chromium_missing", "chromium_revision_incompatible",
        }
        # Failure must not remove other clients' revisions or corrupt the
        # Python venv; in this fresh HOME no usable binary is installed.
        result = status.redacted()
        assert set(result) == {
            "code", "playwright_version", "browser", "read_only",
        }
        assert result["browser"] == "chromium"
        assert test_secret not in repr(status)
        assert test_secret not in repr(result)
        visible = capsys.readouterr()
        assert test_secret not in visible.out + visible.err
    finally:
        proxy.shutdown()
        proxy.server_close()
        runner.join(timeout=3)
