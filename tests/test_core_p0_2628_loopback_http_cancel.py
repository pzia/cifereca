"""P0 #2628: a cancelled Chromium install stops an ACTIVE HTTP transfer.

Only a synthetic, streaming HTTP service bound to 127.0.0.1 is contacted.
The Playwright CLI is replaced at the Popen boundary by a real Python parent
and a real descendant that ignores SIGTERM while reading the local socket.
The production Core install_chromium/_stop code controls the process group.
No Playwright CDN, provider, account, secret or user browsing data is used.
"""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
from threading import Event, Thread
from time import monotonic, sleep
from types import SimpleNamespace

import pytest

from cifereca.plugins import playwright_browser as browser


def _live_linux_process(pid: int) -> bool:
    try:
        status = Path(f"/proc/{pid}/stat").read_text().split(") ", 1)[1][0]
    except FileNotFoundError:
        return False
    return status not in {"Z", "X", "x"}


def test_cancel_ends_inflight_loopback_http_download_and_reaps_children(
    monkeypatch, tmp_path,
):
    if not sys.platform.startswith("linux") or not Path("/proc/self/stat").exists():
        pytest.skip("requires Linux process groups and procfs")

    started_stream = Event()
    disconnected = Event()
    served_routes = []
    chunk = b"FICTITIOUS_CHROMIUM_ARCHIVE_BYTES" * 2048

    class SyntheticCDN(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            # Never record request headers, proxy environment or fake archive.
            pass

        def do_GET(self):
            served_routes.append(self.path)
            if self.path != "/fictional-chromium-archive":
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.end_headers()
            # Keep the HTTP body streaming until the Core kills its client.
            # This is bounded even when cancellation fails.
            for _ in range(600):
                try:
                    self.wfile.write(chunk)
                    self.wfile.flush()
                    started_stream.set()
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    disconnected.set()
                    return
                sleep(.01)

    server = ThreadingHTTPServer(("127.0.0.1", 0), SyntheticCDN)
    server.daemon_threads = True
    server_thread = Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    assert server.server_address[0] == "127.0.0.1"

    marker = tmp_path / "downloader-child.pid"
    cache = tmp_path / "uncreated-browser-cache"
    child_code = (
        "import os,signal,socket,sys\n"
        "from pathlib import Path\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "Path(sys.argv[1]).write_text(f'{os.getpid()}:{os.getpgrp()}')\n"
        "client=socket.create_connection(('127.0.0.1',int(sys.argv[2])),timeout=5)\n"
        "client.sendall(b'GET /fictional-chromium-archive HTTP/1.1\\r\\n"
        "Host: 127.0.0.1\\r\\nConnection: close\\r\\n\\r\\n')\n"
        "while client.recv(65536): pass\n"
    )
    parent_code = (
        "import subprocess,sys,time\n"
        "subprocess.Popen([sys.executable,'-c',sys.argv[1],"
        "sys.argv[2],sys.argv[3]],stdin=subprocess.DEVNULL,"
        "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,"
        "close_fds=True)\n"
        "while True: time.sleep(.1)\n"
    )
    real_popen = subprocess.Popen
    launched = []
    cancelled = Event()
    decision = Event()

    def start_real_fixture(cmd, **kwargs):
        assert cmd == [
            sys.executable, "-m", "playwright", "install",
            "--no-shell", "--no-remove", "chromium",
        ]
        assert kwargs["start_new_session"] is True
        assert kwargs["stdout"] == subprocess.DEVNULL
        assert kwargs["stderr"] == subprocess.DEVNULL
        proc = real_popen(
            [sys.executable, "-c", parent_code, child_code, str(marker),
             str(server.server_port)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, close_fds=True,
            start_new_session=True,
        )
        launched.append(proc)
        return proc

    monkeypatch.delenv("PLAYWRIGHT_DOWNLOAD_HOST", raising=False)
    monkeypatch.delenv("PLAYWRIGHT_CHROMIUM_DOWNLOAD_HOST", raising=False)
    monkeypatch.setenv(
        "HTTPS_PROXY", "http://FAKE_PROXY_SECRET_NEVER_EXPOSE@invalid.invalid:3128",
    )
    monkeypatch.setattr(
        browser, "inspect_chromium",
        lambda **_kwargs: browser.BrowserState("chromium_missing", "1.55.0"),
    )
    monkeypatch.setattr(browser, "_cache_path", lambda **_kwargs: cache)
    monkeypatch.setattr(browser.sys, "prefix", str(tmp_path / "managed-venv"))
    monkeypatch.setattr(browser.sys, "base_prefix", "/usr")
    monkeypatch.setattr(
        browser.shutil, "disk_usage",
        lambda _path: SimpleNamespace(free=4 * 1024**3),
    )
    monkeypatch.setattr(browser.subprocess, "Popen", start_real_fixture)

    def stop_after_real_response():
        if started_stream.wait(timeout=7):
            decision.set()
            cancelled.set()

    cancel_thread = Thread(target=stop_after_real_response, daemon=True)
    cancel_thread.start()
    try:
        events = []
        outcome = browser.install_chromium(
            consent=True, cancelled=cancelled, progress=events.append, timeout=12,
        )
        assert outcome.code == "cancelled", outcome.redacted()
        assert decision.is_set(), "did not wait for an active HTTP response"
        assert served_routes == ["/fictional-chromium-archive"]
        assert events == ["starting"]
        assert marker.is_file(), "child downloader never connected"
        child_pid, child_pgid = map(int, marker.read_text().split(":"))
        assert launched and child_pgid == launched[0].pid

        deadline = monotonic() + 4
        while not disconnected.is_set() and monotonic() < deadline:
            sleep(.02)
        assert disconnected.is_set(), "HTTP stream remained open after cancellation"

        deadline = monotonic() + 3
        while _live_linux_process(child_pid) and monotonic() < deadline:
            sleep(.02)
        assert not _live_linux_process(child_pid), "downloader survived cancellation"
        assert launched[0].poll() is not None
        assert not cache.exists()
        assert not browser._INSTALL_LOCK.locked()
        assert "FAKE_PROXY_SECRET_NEVER_EXPOSE" not in repr(outcome)
        assert "FAKE_PROXY_SECRET_NEVER_EXPOSE" not in str(outcome.redacted())
    finally:
        cancelled.set()
        cancel_thread.join(timeout=2)
        for proc in launched:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=3)
