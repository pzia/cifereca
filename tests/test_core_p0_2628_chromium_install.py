"""P0 #2628 — browser install is an opt-in Core operation, never networked in CI."""
from __future__ import annotations

import ast
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from threading import Event
from types import SimpleNamespace

from cifereca.plugins import playwright_browser as browser


def test_absent_playwright_never_starts_driver_or_installer(monkeypatch):
    monkeypatch.setattr(browser, "_version", lambda: None)
    monkeypatch.setattr(browser, "_executable", lambda: (_ for _ in ()).throw(AssertionError("driver started")))
    monkeypatch.setattr(browser.subprocess, "Popen", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("installer started")))
    assert browser.inspect_chromium().code == "playwright_missing"
    assert browser.install_chromium(consent=False).code == "consent_required"
    assert browser.install_chromium(consent=True).code == "playwright_missing"


def test_browser_cache_rejects_foreign_relative_and_symlinked_paths(monkeypatch, tmp_path):
    home = tmp_path / "private-home"
    home.mkdir()
    monkeypatch.setattr(browser.Path, "home", lambda: home)
    for bad in ("0", "relative", str(tmp_path / "foreign"), str(home / "x" / ".." / "z")):
        monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", bad)
        try:
            browser._cache_path()
            assert False, f"unsafe cache accepted: {bad}"
        except ValueError:
            pass
    link = home / "alias"
    link.symlink_to(tmp_path, target_is_directory=True)
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(link / "ms-playwright"))
    try:
        browser._cache_path()
        assert False, "symlinked cache accepted"
    except ValueError:
        pass


def test_inspection_is_read_only_and_coupled_to_active_revision(monkeypatch, tmp_path):
    home = tmp_path / "home"
    cache = home / ".cache" / "ms-playwright"
    cache.mkdir(parents=True)
    monkeypatch.setattr(browser.Path, "home", lambda: home)
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.setattr(browser, "_version", lambda: "1.55.0")
    exe = cache / "chromium-123" / "chrome-linux64" / "chrome"
    monkeypatch.setattr(browser, "_executable", lambda: exe)
    assert browser.inspect_chromium().code == "chromium_missing"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"synthetic non-browser")
    exe.chmod(0o755)
    assert browser.inspect_chromium().code == "chromium_installed_unverified"
    # A different official revision makes the *expected* one unavailable.
    monkeypatch.setattr(browser, "_executable", lambda: cache / "chromium-456" / "chrome-linux64" / "chrome")
    assert browser.inspect_chromium().code == "chromium_revision_incompatible"
    # A symlinked revision may not redirect a browser launch.
    (cache / "chromium-456").symlink_to(exe.parent.parent, target_is_directory=True)
    assert browser.inspect_chromium().code == "browser_cache_unavailable"


def test_opt_in_installer_is_exact_playwright_chromium_without_shell_or_os_packages(monkeypatch, tmp_path):
    home = tmp_path / "home"
    cache = home / ".cache" / "ms-playwright"
    cache.mkdir(parents=True)
    monkeypatch.setattr(browser, "_cache_path", lambda: cache)
    monkeypatch.setattr(browser, "inspect_chromium", lambda *, test_launch=False: (
        browser.BrowserState("ready", "1.55.0") if test_launch
        else browser.BrowserState("chromium_missing", "1.55.0")
    ))
    monkeypatch.setattr(browser.sys, "prefix", str(tmp_path / "venv"))
    monkeypatch.setattr(browser.sys, "base_prefix", "/usr")
    monkeypatch.setattr(browser.shutil, "disk_usage", lambda _: SimpleNamespace(free=4*1024**3))
    calls = []
    class Process:
        def poll(self): return 0
        def wait(self, timeout=None): return 0
    def fake_popen(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return Process()
    monkeypatch.setattr(browser.subprocess, "Popen", fake_popen)
    assert browser.install_chromium(consent=False).code == "consent_required"
    assert calls == []
    phases = []
    result = browser.install_chromium(consent=True, progress=phases.append)
    assert result.code == "ready"
    assert phases == ["starting", "verifying"]
    assert len(calls) == 1
    cmd, kwargs = calls[0]
    assert cmd == [browser.sys.executable, "-m", "playwright", "install",
                   "--no-shell", "--no-remove", "chromium"]
    assert kwargs["start_new_session"] is True
    assert kwargs["stdout"] == browser.subprocess.DEVNULL
    assert kwargs["stderr"] == browser.subprocess.DEVNULL
    assert kwargs["stdin"] == browser.subprocess.DEVNULL
    assert kwargs.get("shell") is None
    assert "chromium" not in result.redacted()["playwright_version"]


def test_custom_browser_mirror_requires_separate_administrative_consent(monkeypatch, tmp_path):
    monkeypatch.setenv("PLAYWRIGHT_CHROMIUM_DOWNLOAD_HOST", "https://mirror.invalid")
    monkeypatch.setattr(browser, "inspect_chromium",
                        lambda **_: browser.BrowserState("chromium_missing", "1.55"))
    monkeypatch.setattr(browser, "_cache_path", lambda: tmp_path)
    monkeypatch.setattr(browser.sys, "prefix", str(tmp_path / "venv"))
    monkeypatch.setattr(browser.sys, "base_prefix", "/usr")
    monkeypatch.setattr(browser.shutil, "disk_usage",
                        lambda _: SimpleNamespace(free=4*1024**3))
    monkeypatch.setattr(browser.subprocess, "Popen",
                        lambda *_a, **_kw: (_ for _ in ()).throw(
                            AssertionError("unreviewed archive source used")))
    assert browser.install_chromium(consent=True).code == "browser_download_source_unapproved"


def test_install_is_cancelled_without_a_single_subprocess(monkeypatch):
    cancelled = Event()
    cancelled.set()
    monkeypatch.setattr(browser.subprocess, "Popen", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("unexpected Popen")))
    assert browser.install_chromium(consent=True, cancelled=cancelled).code == "cancelled"


def test_low_disk_and_unmanaged_interpreter_fail_closed_before_download(monkeypatch, tmp_path):
    monkeypatch.setattr(browser, "inspect_chromium", lambda **_: browser.BrowserState("chromium_missing", "1.55"))
    monkeypatch.setattr(browser, "_cache_path", lambda: tmp_path)
    monkeypatch.setattr(browser.shutil, "disk_usage", lambda _: SimpleNamespace(free=42))
    monkeypatch.setattr(browser.subprocess, "Popen", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("download attempted")))
    monkeypatch.setattr(browser.sys, "prefix", "/usr")
    monkeypatch.setattr(browser.sys, "base_prefix", "/usr")
    assert browser.install_chromium(consent=True).code == "venv_required"
    monkeypatch.setattr(browser.sys, "prefix", str(tmp_path / "venv"))
    assert browser.install_chromium(consent=True).code == "disk_space_insufficient"


def test_cancel_after_start_terminates_installer_group(monkeypatch, tmp_path):
    cancelled = Event()
    monkeypatch.setattr(browser, "inspect_chromium", lambda **_: browser.BrowserState("chromium_missing", "1.55"))
    monkeypatch.setattr(browser, "_cache_path", lambda: tmp_path)
    monkeypatch.setattr(browser.sys, "prefix", str(tmp_path / "venv"))
    monkeypatch.setattr(browser.sys, "base_prefix", "/usr")
    monkeypatch.setattr(browser.shutil, "disk_usage", lambda _: SimpleNamespace(free=4*1024**3))
    stopped = []
    class Process:
        def poll(self): return None
    monkeypatch.setattr(browser.subprocess, "Popen", lambda *a, **kw: Process())
    monkeypatch.setattr(browser, "_stop", lambda proc: stopped.append(proc))
    monkeypatch.setattr(browser.time, "sleep", lambda _: cancelled.set())
    assert browser.install_chromium(consent=True, cancelled=cancelled).code == "cancelled"
    assert len(stopped) == 1


def test_gtk_install_has_separate_consent_async_progress_and_cancel():
    gui = Path(__file__).resolve().parents[1] / "src/cifereca/gui/plugin_manager_page.py"
    root = ast.parse(gui.read_text(encoding="utf-8"))
    cls = next(n for n in root.body if isinstance(n, ast.ClassDef) and n.name == "PluginManagerPage")
    methods = {n.name: ast.unparse(n) for n in cls.body if isinstance(n, ast.FunctionDef)}
    assert "_confirm_chromium_install" in methods
    assert 'response == \'install\'' in methods["_confirm_chromium_install"].replace('"', "'")
    assert "consent=True" in methods["_install_chromium"]
    assert "cancelled=self._chromium_cancel" in methods["_install_chromium"]
    assert "Thread(" in methods["_install_chromium"]
    assert "GLib.idle_add(self._finish_chromium_operation" in methods["_install_chromium"]
    assert "self._chromium_cancel.set()" in methods["_plugin_row"]
    assert "install_chromium" not in methods["refresh"]
    assert "_chromium_operation_running" in methods["_install_python_dependencies"]
    assert "getattr(self.window, '_closed', False)" in (
        methods["_finish_chromium_operation"].replace('"', "'")
    )


def test_cache_unwritable_parent_fails_before_download(monkeypatch, tmp_path):
    home = tmp_path / "private-home"
    home.mkdir()
    cache = home / ".cache" / "ms-playwright"
    monkeypatch.setattr(browser.Path, "home", lambda: home)
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(cache))
    monkeypatch.setattr(browser, "_version", lambda: "1.55.0")
    monkeypatch.setattr(browser, "_executable", lambda: cache / "chromium-123" / "chrome")
    monkeypatch.setattr(browser.sys, "prefix", str(tmp_path / "managed-venv"))
    monkeypatch.setattr(browser.sys, "base_prefix", "/usr")
    original_access = browser.os.access

    def inaccessible(path, mode):
        if path == home and mode & os.W_OK:
            return False
        return original_access(path, mode)

    monkeypatch.setattr(browser.os, "access", inaccessible)
    # Browser discovery is read-only: a read-only HOME is not a broken
    # Chromium installation. Download, however, must fail before Popen.
    assert browser.inspect_chromium().code == "chromium_missing"
    monkeypatch.setattr(browser.subprocess, "Popen", lambda *_a, **_kw: (
        (_ for _ in ()).throw(AssertionError("unexpected download"))
    ))
    assert browser.install_chromium(consent=True).code == "browser_cache_unavailable"
    assert not cache.exists()


def test_readonly_installed_chromium_remains_visible_to_inspection(monkeypatch, tmp_path):
    """A shared/immutable but valid existing browser remains usable."""
    home = tmp_path / "home"
    cache = home / ".cache" / "ms-playwright"
    exe = cache / "chromium-123" / "chrome-linux64" / "chrome"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"synthetic executable, never launched")
    exe.chmod(0o755)
    monkeypatch.setattr(browser.Path, "home", lambda: home)
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(cache))
    monkeypatch.setattr(browser, "_version", lambda: "1.55.0")
    monkeypatch.setattr(browser, "_executable", lambda: exe)
    original_access = browser.os.access

    def readonly(path, mode):
        if path == cache and mode & os.W_OK:
            return False
        return original_access(path, mode)

    monkeypatch.setattr(browser.os, "access", readonly)
    monkeypatch.setattr(browser.subprocess, "Popen", lambda *_args, **_kw: (
        (_ for _ in ()).throw(AssertionError("read-only inspection spawned process"))
    ))
    assert browser.inspect_chromium().code == "chromium_installed_unverified"
    assert browser.install_chromium(consent=False).code == "consent_required"
    assert exe.is_file()


def test_readonly_existing_cache_refuses_download_without_mutation(monkeypatch, tmp_path):
    home = tmp_path / "home"
    cache = home / ".cache" / "ms-playwright"
    cache.mkdir(parents=True)
    monkeypatch.setattr(browser.Path, "home", lambda: home)
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(cache))
    monkeypatch.setattr(browser, "_version", lambda: "1.55.0")
    monkeypatch.setattr(browser, "_executable",
                        lambda: cache / "chromium-123" / "chrome")
    monkeypatch.setattr(browser.sys, "prefix", str(tmp_path / "managed-venv"))
    monkeypatch.setattr(browser.sys, "base_prefix", "/usr")
    original_access = browser.os.access

    def readonly(path, mode):
        if path == cache and mode & os.W_OK:
            return False
        return original_access(path, mode)

    monkeypatch.setattr(browser.os, "access", readonly)
    monkeypatch.setattr(browser.subprocess, "Popen", lambda *_args, **_kw: (
        (_ for _ in ()).throw(AssertionError("download into read-only cache"))
    ))
    assert browser.inspect_chromium().code == "chromium_missing"
    assert browser.install_chromium(consent=True).code == "browser_cache_unavailable"
    assert tuple(cache.iterdir()) == ()



def test_cancel_reaps_real_installer_process_group_when_parent_exits_first(tmp_path):
    """Exercise Linux signals: Node-like child ignores TERM, launcher exits.

    The child is an inert Python sleeper in a fresh process group; no shell,
    browser, network call, credentials or provider are used. The test fails
    with the old _stop implementation which returned when only the parent
    had exited, leaving its independent downloader child alive.
    """
    import pytest

    if not sys.platform.startswith("linux") or not Path("/proc/self/stat").is_file():
        pytest.skip("requires Linux /proc and POSIX process groups")

    marker = tmp_path / "synthetic-child.pid"
    child_script = (
        "import os,signal,sys,time\n"
        "from pathlib import Path\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "Path(sys.argv[1]).write_text(str(os.getpid()))\n"
        "while True: time.sleep(.1)\n"
    )
    parent_script = (
        "import subprocess,sys,time\n"
        "from pathlib import Path\n"
        "marker = Path(sys.argv[1])\n"
        "subprocess.Popen([sys.executable, '-c', sys.argv[2], str(marker)], "
        "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,"
        "stderr=subprocess.DEVNULL,close_fds=True)\n"
        "until = time.monotonic() + 5\n"
        "while not marker.is_file() and time.monotonic() < until: time.sleep(.02)\n"
        "if not marker.is_file(): sys.exit(3)\n"
        "while True: time.sleep(.1)\n"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", parent_script, str(marker), child_script],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, close_fds=True, start_new_session=True,
    )

    def child_is_live(pid):
        stat_file = Path(f"/proc/{pid}/stat")
        try:
            state = stat_file.read_text().split(") ", 1)[1][0]
        except FileNotFoundError:
            return False
        # The child is orphaned when the parent exits. A zombie is inert,
        # awaiting reaping by PID 1; os.kill(pid, 0) would return success.
        return state not in ("Z", "X", "x")

    try:
        deadline = time.monotonic() + 8
        while not marker.is_file() and time.monotonic() < deadline:
            time.sleep(.03)
        assert marker.is_file(), "synthetic downloader child never started"
        child_pid = int(marker.read_text())
        assert child_pid != proc.pid
        assert child_is_live(child_pid)
        assert os.getpgid(child_pid) == proc.pid

        browser._stop(proc)
        assert proc.poll() is not None
        deadline = time.monotonic() + 3
        while child_is_live(child_pid) and time.monotonic() < deadline:
            time.sleep(.02)
        assert not child_is_live(child_pid), (
            "installer download child survived even though the parent exited"
        )
    finally:
        # Ensure a failing test does not leave a synthetic subprocess behind.
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=3)



def test_failed_playwright_cli_also_stops_descendants_and_redacts_proxy(
    monkeypatch, tmp_path,
):
    """Nonzero launcher exit cannot leave a downloader child unsupervised."""
    monkeypatch.delenv("PLAYWRIGHT_DOWNLOAD_HOST", raising=False)
    monkeypatch.delenv("PLAYWRIGHT_CHROMIUM_DOWNLOAD_HOST", raising=False)
    monkeypatch.setenv("HTTPS_PROXY", "http://FAKE_SECRET_CI@proxy.invalid:3128")
    monkeypatch.setattr(
        browser, "inspect_chromium",
        lambda **_: browser.BrowserState("chromium_missing", "1.55.0"),
    )
    monkeypatch.setattr(browser, "_cache_path", lambda: tmp_path)
    monkeypatch.setattr(browser.sys, "prefix", str(tmp_path / "venv"))
    monkeypatch.setattr(browser.sys, "base_prefix", "/usr")
    monkeypatch.setattr(
        browser.shutil, "disk_usage",
        lambda _: SimpleNamespace(free=4 * 1024**3),
    )

    class FailedInstaller:
        def poll(self):
            return 23

        def wait(self, timeout=None):
            return 23

    installer = FailedInstaller()
    spawned = []
    stopped = []

    def spawn(cmd, **kwargs):
        spawned.append((cmd, kwargs))
        return installer

    monkeypatch.setattr(browser.subprocess, "Popen", spawn)
    monkeypatch.setattr(browser, "_stop", lambda proc: stopped.append(proc))
    phases = []
    result = browser.install_chromium(consent=True, progress=phases.append)
    assert result.code == "download_failed"
    assert phases == ["starting"]
    assert len(spawned) == 1
    assert stopped == [installer], "failed Playwright subprocess group not reaped"
    assert spawned[0][1]["stderr"] == subprocess.DEVNULL
    assert spawned[0][1]["stdout"] == subprocess.DEVNULL
    assert spawned[0][1]["start_new_session"] is True
    assert "FAKE_SECRET_CI" not in repr(result)
    assert "FAKE_SECRET_CI" not in str(result.redacted())
