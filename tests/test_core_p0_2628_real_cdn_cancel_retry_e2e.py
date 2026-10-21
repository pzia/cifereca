"""P0 #2628: abort an actual official Playwright install, then recover.

CI opt-in ONLY, using a fresh disposable Linux HOME/cache and installed
Playwright Python. The first official CLI is stopped while the real installer
process is running; the same cache must subsequently accept a complete,
version-coupled Chromium installation and launch. No accounts, profiles,
providers, screenshots, HAR, ZIP or user captures. This proves cancellation
of a real CLI attempt, not that any minimum CDN byte count was transferred.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
from threading import Event, Thread
from time import sleep

import pytest

from cifereca.plugins import playwright_browser as browser


pytestmark = pytest.mark.skipif(
    os.environ.get("CIFERECA_REAL_CDN_CANCEL_E2E") != "1",
    reason="requires explicit CI consent to contact official Playwright CDN",
)


def test_real_cli_cancelled_while_running_then_same_cache_recovers(
    monkeypatch,
):
    assert sys.platform.startswith("linux") and sys.prefix != sys.base_prefix
    assert Path.home().name == "empty-cancel-home"
    assert os.environ.get("PLAYWRIGHT_BROWSERS_PATH") is None
    assert os.environ.get("PLAYWRIGHT_DOWNLOAD_HOST") is None
    assert os.environ.get("PLAYWRIGHT_CHROMIUM_DOWNLOAD_HOST") is None
    cache = Path.home() / ".cache" / "ms-playwright"
    assert not cache.exists()
    initial = browser.inspect_chromium()
    assert initial.code == "chromium_missing" and initial.version

    # Discovery and a missing consent flag are strictly no-ops.
    assert browser.install_chromium(consent=False).code == "consent_required"
    assert not cache.exists()

    expected = [
        sys.executable, "-m", "playwright", "install",
        "--no-shell", "--no-remove", "chromium",
    ]
    original_popen = subprocess.Popen
    subprocesses = []
    first_spawned = Event()
    cancelled = Event()
    cancellation_while_live = Event()

    def observe_official_install(cmd, *args, **kwargs):
        if cmd == expected:
            assert kwargs.get("start_new_session") is True
            assert kwargs["stdout"] == subprocess.DEVNULL
            assert kwargs["stderr"] == subprocess.DEVNULL
            proc = original_popen(cmd, *args, **kwargs)
            subprocesses.append(proc)
            first_spawned.set()
            return proc
        # Probing Playwright's local driver is unrelated to the installer CLI.
        return original_popen(cmd, *args, **kwargs)

    monkeypatch.setattr(browser.subprocess, "Popen", observe_official_install)

    def cancel_after_real_launch():
        if not first_spawned.wait(timeout=20):
            return
        # Give the actual Python/Node launcher a brief window to start work,
        # but cancel before its large cold-cache Chromium install can finish.
        sleep(.2)
        if subprocesses[0].poll() is None:
            cancellation_while_live.set()
            cancelled.set()

    worker = Thread(target=cancel_after_real_launch, daemon=True)
    worker.start()
    try:
        phases = []
        first = browser.install_chromium(
            consent=True, cancelled=cancelled, progress=phases.append,
            timeout=90,
        )
        assert cancellation_while_live.is_set(), (
            "official installer already exited before cancellation was requested"
        )
        assert first.code == "cancelled", first.redacted()
        assert phases == ["starting"]
        assert len(subprocesses) == 1
        assert subprocesses[0].poll() is not None
        assert browser._INSTALL_LOCK.locked() is False
        assert browser.inspect_chromium().code in {
            "chromium_missing", "chromium_revision_incompatible",
        }, "Chromium unexpectedly finished installing before cancellation"

        # Do NOT delete Playwright's partial cache. A genuine retry into the
        # exact same HOME exercises recovery rather than a clean reinstall.
        retry_phases = []
        retry = browser.install_chromium(
            consent=True, progress=retry_phases.append, timeout=840,
        )
        assert retry.code == "ready", retry.redacted()
        assert retry.version == initial.version
        assert retry_phases == ["starting", "verifying"]
        assert browser.inspect_chromium(test_launch=True).code == "ready"
        assert len(subprocesses) == 2, "retry did not invoke the official CLI"
        assert browser._INSTALL_LOCK.locked() is False

        # No headless shell, Firefox or WebKit is provisioned by Core.
        names = {entry.name for entry in cache.iterdir()}
        assert any(x.startswith("chromium-") for x in names)
        assert not any(x.startswith((
            "chromium_headless_shell-", "firefox-", "webkit-",
        )) for x in names)
    finally:
        cancelled.set()
        worker.join(timeout=2)
        # No foreign browser cache, user files or process is touched.
        for proc in subprocesses:
            if proc.poll() is None:
                try:
                    import signal
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)
