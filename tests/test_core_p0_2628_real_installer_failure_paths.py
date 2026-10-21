"""P0 #2628: real POSIX worker group through Core install_chromium failure paths.

No Playwright installation or network is performed. A synthetic Python parent
emulates the CLI, spawning a child that ignores SIGTERM. The actual Core
install_chromium -> _stop code must leave no downloader still executing.
"""
from __future__ import annotations

import os
from pathlib import Path
import signal
import subprocess
import sys
from threading import Event, Thread
from time import monotonic, sleep
from types import SimpleNamespace

import pytest

from cifereca.plugins import playwright_browser as browser


@pytest.mark.parametrize(
    ("mode", "expected"),
    (
        ("cancel", "cancelled"),
        ("timeout", "download_timeout"),
        ("failure", "download_failed"),
    ),
)
def test_real_linux_installer_group_stops_on_every_failure(
    monkeypatch, tmp_path, mode, expected,
):
    if not sys.platform.startswith("linux") or not Path("/proc/self/stat").exists():
        pytest.skip("Linux process groups and /proc required")

    # Force only synthetic, explicit, bounded work. All paths remain local.
    monkeypatch.delenv("PLAYWRIGHT_DOWNLOAD_HOST", raising=False)
    monkeypatch.delenv("PLAYWRIGHT_CHROMIUM_DOWNLOAD_HOST", raising=False)
    monkeypatch.setattr(
        browser, "inspect_chromium",
        lambda **_: browser.BrowserState("chromium_missing", "1.55.0"),
    )
    cache = tmp_path / "empty-browser-cache"
    monkeypatch.setattr(browser, "_cache_path", lambda **_kwargs: cache)
    monkeypatch.setattr(browser.sys, "prefix", str(tmp_path / "core-venv"))
    monkeypatch.setattr(browser.sys, "base_prefix", "/usr")
    monkeypatch.setattr(
        browser.shutil, "disk_usage",
        lambda _: SimpleNamespace(free=4 * 1024**3),
    )
    marker = tmp_path / "synthetic-worker.pid"
    # Parent and child are in the SAME new session/process group. The child
    # deliberately ignores SIGTERM, mirroring a downloader lagging behind its
    # launcher. Nothing creates a browser, opens a socket or downloads data.
    child_code = (
        "import os,signal,sys,time\n"
        "from pathlib import Path\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "Path(sys.argv[1]).write_text(f'{os.getpid()}:{os.getpgrp()}')\n"
        "while True: time.sleep(.1)\n"
    )
    parent_code = (
        "import subprocess,sys,time\n"
        "from pathlib import Path\n"
        "marker = Path(sys.argv[1])\n"
        "subprocess.Popen([sys.executable, '-c', sys.argv[2], str(marker)], "
        "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,"
        "stderr=subprocess.DEVNULL,close_fds=True)\n"
        "until = time.monotonic() + 5\n"
        "while not marker.is_file() and time.monotonic() < until: time.sleep(.02)\n"
        "if not marker.is_file(): sys.exit(8)\n"
        "if sys.argv[3] == 'failure': sys.exit(41)\n"
        "while True: time.sleep(.1)\n"
    )

    # Intercept only the invocation of the official Playwright CLI. The
    # spawned fixture process is real and supervised by the production code.
    real_popen = subprocess.Popen
    spawned = []
    cancelled = Event()
    ready = Event()

    def spawn(cmd, **kwargs):
        assert cmd == [
            sys.executable, "-m", "playwright", "install",
            "--no-shell", "--no-remove", "chromium",
        ]
        assert kwargs["stdin"] == subprocess.DEVNULL
        assert kwargs["stdout"] == subprocess.DEVNULL
        assert kwargs["stderr"] == subprocess.DEVNULL
        assert kwargs["start_new_session"] is True
        proc = real_popen(
            [sys.executable, "-c", parent_code, str(marker), child_code, mode],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, close_fds=True, start_new_session=True,
        )
        spawned.append(proc)
        return proc

    monkeypatch.setattr(browser.subprocess, "Popen", spawn)

    def is_live(pid):
        try:
            state = Path(f"/proc/{pid}/stat").read_text().split(") ", 1)[1][0]
        except FileNotFoundError:
            return False
        return state not in {"Z", "X", "x"}

    def cancel_when_started():
        until = monotonic() + 6
        while not marker.exists() and monotonic() < until:
            sleep(.02)
        if marker.exists():
            ready.set()
            cancelled.set()

    cancel_thread = None
    if mode == "cancel":
        cancel_thread = Thread(target=cancel_when_started, daemon=True)
        cancel_thread.start()

    try:
        phases = []
        result = browser.install_chromium(
            consent=True, cancelled=cancelled,
            progress=phases.append, timeout=1 if mode == "timeout" else 8,
        )
        assert len(spawned) == 1
        assert marker.is_file(), "real worker was not spawned"
        child_pid, child_group = map(int, marker.read_text().split(":"))
        assert child_group == spawned[0].pid, "fixture lost its installer group"
        assert result.code == expected
        assert phases == ["starting"]
        assert not cache.exists()
        assert browser._INSTALL_LOCK.locked() is False

        until = monotonic() + 3
        while is_live(child_pid) and monotonic() < until:
            sleep(.02)
        assert not is_live(child_pid), (
            "Core returned a redacted status while its downloader was running"
        )
        assert spawned[0].poll() is not None
        if mode == "cancel":
            assert ready.is_set() and cancelled.is_set()
    finally:
        for proc in spawned:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)
        if cancel_thread is not None:
            cancel_thread.join(timeout=2)
