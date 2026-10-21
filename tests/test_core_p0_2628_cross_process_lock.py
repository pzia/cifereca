"""#2628: two Cifereca processes must never download into HOME concurrently.

Real Linux directory-flock contention with a throwaway Python holder process;
the Playwright subprocess is explicitly FORBIDDEN in these offline tests.
"""
from __future__ import annotations

import os
from pathlib import Path
import select
import subprocess
import sys
from threading import Event
from types import SimpleNamespace

import pytest

from cifereca.plugins import playwright_browser as browser

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="Linux O_DIRECTORY and advisory directory flock required",
)


def test_second_process_refuses_parallel_download_without_cache_mutation(
    monkeypatch, tmp_path,
):
    home = tmp_path / "private-home"
    home.mkdir()
    cache = home / ".cache" / "ms-playwright"

    # This is a real SEPARATE process. It holds HOME's directory inode
    # flock but creates no lockfile and opens no socket. stdout is a single
    # synthetic readiness marker with no user or browser data.
    holder_script = (
        "import fcntl,os,sys\n"
        "fd=os.open(sys.argv[1],os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)\n"
        "fcntl.flock(fd,fcntl.LOCK_EX)\n"
        "print('LOCKED',flush=True)\n"
        "sys.stdin.readline()\n"
    )
    holder = subprocess.Popen(
        [sys.executable, "-c", holder_script, str(home)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, text=True, close_fds=True,
        start_new_session=True,
    )
    try:
        assert select.select([holder.stdout], [], [], 6)[0], (
            "synthetic holder never acquired HOME lock"
        )
        assert holder.stdout.readline().strip() == "LOCKED"
        monkeypatch.setattr(browser.Path, "home", lambda: home)
        monkeypatch.setattr(browser, "inspect_chromium",
                            lambda **_: browser.BrowserState("chromium_missing", "1.55"))
        monkeypatch.setattr(browser, "_cache_path", lambda: cache)
        monkeypatch.setattr(browser.sys, "prefix", str(tmp_path / "managed-venv"))
        monkeypatch.setattr(browser.sys, "base_prefix", "/usr")
        monkeypatch.setattr(browser.shutil, "disk_usage",
                            lambda _: SimpleNamespace(free=4 * 1024**3))
        # Never accidentally run a real Playwright CLI in this test.
        monkeypatch.setattr(browser.subprocess, "Popen", lambda *_args, **_kwargs: (
            (_ for _ in ()).throw(AssertionError("unexpected browser download"))
        ))
        phases = []
        assert browser.install_chromium(consent=False).code == "consent_required"
        second = browser.install_chromium(consent=True, progress=phases.append)
        assert second.code == "installation_already_running"
        assert phases == []
        assert not cache.exists()
        assert browser._INSTALL_LOCK.locked() is False
    finally:
        holder.stdin.close()
        try:
            holder.wait(timeout=6)
        except subprocess.TimeoutExpired:
            holder.terminate()
            holder.wait(timeout=3)

    # POSIX flock is released on process exit, without lockfile cleanup.
    lock_fd = browser._acquire_install_process_lock()
    try:
        assert lock_fd >= 0
    finally:
        os.close(lock_fd)
    assert tuple(home.iterdir()) == ()


def test_cancel_during_preflight_progress_never_spawns_cli(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(browser.Path, "home", lambda: home)
    monkeypatch.setattr(browser, "_cache_path", lambda: home / ".cache" / "ms-playwright")
    monkeypatch.setattr(browser, "inspect_chromium",
                        lambda **_: browser.BrowserState("chromium_missing", "1.55"))
    monkeypatch.setattr(browser.sys, "prefix", str(tmp_path / "managed-venv"))
    monkeypatch.setattr(browser.sys, "base_prefix", "/usr")
    monkeypatch.setattr(browser.shutil, "disk_usage",
                        lambda _: SimpleNamespace(free=4 * 1024**3))
    monkeypatch.setattr(browser.subprocess, "Popen", lambda *_args, **_kwargs: (
        (_ for _ in ()).throw(AssertionError("download after cancellation"))
    ))
    cancelled = Event()
    phases = []

    def user_cancel(phase):
        phases.append(phase)
        cancelled.set()

    assert browser.install_chromium(
        consent=True, cancelled=cancelled, progress=user_cancel,
    ).code == "cancelled"
    assert phases == ["starting"]
    assert browser._INSTALL_LOCK.locked() is False
    assert tuple(home.iterdir()) == ()


def test_other_process_completed_before_lock_acquired_is_not_redownloaded(
    monkeypatch, tmp_path,
):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(browser.Path, "home", lambda: home)
    monkeypatch.setattr(browser, "_cache_path", lambda: home / ".cache" / "ms-playwright")
    states = iter(("chromium_missing", "chromium_installed_unverified", "ready"))
    calls = []

    def inspect_chromium(*, test_launch=False):
        calls.append(test_launch)
        return browser.BrowserState(next(states), "1.55")

    monkeypatch.setattr(browser, "inspect_chromium", inspect_chromium)
    monkeypatch.setattr(browser.sys, "prefix", str(tmp_path / "managed-venv"))
    monkeypatch.setattr(browser.sys, "base_prefix", "/usr")
    monkeypatch.setattr(browser.shutil, "disk_usage",
                        lambda _: SimpleNamespace(free=4 * 1024**3))
    monkeypatch.setattr(browser.subprocess, "Popen", lambda *_args, **_kwargs: (
        (_ for _ in ()).throw(AssertionError("redundant Chromium installation"))
    ))
    phases = []
    assert browser.install_chromium(
        consent=True, progress=phases.append,
    ).code == "ready"
    assert calls == [False, False, True]
    assert phases == []
    assert tuple(home.iterdir()) == ()
