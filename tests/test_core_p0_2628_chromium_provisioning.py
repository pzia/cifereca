"""P0 #2628: Core-owned, consented Chromium installer; offline synthetic tests."""
from __future__ import annotations

import ast
from pathlib import Path
from threading import Event
from types import SimpleNamespace as NS

from cifereca.plugins import chromium_provisioning as browser

ROOT = Path(__file__).resolve().parents[1]


def test_probe_is_offline_bounded_and_never_exposes_private_paths(monkeypatch):
    commands = []
    monkeypatch.setattr(browser, "_python_version", lambda: "1.50.0")

    def fake_run(cmd, **options):
        commands.append((cmd, options))
        assert cmd[:2] == [browser.sys.executable, "-c"]
        assert options["timeout"] == browser.PROBE_TIMEOUT
        assert options["stderr"] == browser.subprocess.DEVNULL
        return NS(returncode=0, stdout="missing\n")

    monkeypatch.setattr(browser.subprocess, "run", fake_run)
    result = browser.chromium_status()
    assert result == {
        "state": "chromium_missing", "version": "1.50.0",
        "native_libraries": "unverified",
    }
    assert len(commands) == 1
    assert "PRIVATE" not in repr(result)


def test_missing_playwright_requires_no_probe(monkeypatch):
    monkeypatch.setattr(browser, "_python_version", lambda: "")

    def forbidden(*_args, **_kwargs):
        raise AssertionError("browser probe must not run")

    monkeypatch.setattr(browser.subprocess, "run", forbidden)
    assert browser.chromium_status()["state"] == "playwright_missing"
    assert browser.install_chromium(consented=True) == "playwright_missing"


def test_no_consent_or_cancel_means_zero_side_effects(monkeypatch):
    monkeypatch.setattr(browser, "chromium_status", lambda: (_ for _ in ()).throw(
        AssertionError("no probe without consent")))
    assert browser.install_chromium() == "consent_required"
    assert browser.install_chromium(consented=1) == "consent_required"
    cancel = Event()
    cancel.set()
    assert browser.install_chromium(consented=True, cancel=cancel) == "cancelled"


def test_cache_refuses_symlinks_relative_paths_and_package_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "relative/cache")
    assert browser._cache_location() is None
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "0")
    assert browser._cache_location() is None
    real = tmp_path / "real"
    real.mkdir()
    redirect = tmp_path / "symlink"
    redirect.symlink_to(real, target_is_directory=True)
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(redirect / "chromium"))
    assert browser._cache_location() is None
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(real / "chromium"))
    assert browser._cache_location() == real / "chromium"


def test_installs_only_official_headed_chromium_no_gc_or_raw_output(tmp_path, monkeypatch):
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path / "browser-cache"))
    status = iter(("chromium_missing", "ready"))
    monkeypatch.setattr(browser, "chromium_status", lambda: {"state": next(status)})
    monkeypatch.setattr(browser.shutil, "disk_usage", lambda _: NS(free=2**40))
    commands = []

    class FakeProcess:
        returncode = 0
        pid = 1500

        def poll(self):
            return self.returncode

    def popen(cmd, **kwargs):
        commands.append((cmd, kwargs))
        return FakeProcess()

    monkeypatch.setattr(browser.subprocess, "Popen", popen)
    assert browser.install_chromium(consented=True) == "ready"
    assert len(commands) == 1
    command, options = commands[0]
    assert command == [
        browser.sys.executable, "-m", "playwright", "install",
        "--no-shell", "--no-remove", "chromium",
    ]
    assert options["stdin"] == browser.subprocess.DEVNULL
    assert options["stdout"] == browser.subprocess.DEVNULL
    assert options["stderr"] == browser.subprocess.DEVNULL
    assert options["start_new_session"] is True
    assert options["env"]["PLAYWRIGHT_SKIP_BROWSER_GC"] == "1"
    assert "--with-deps" not in command
    assert not list(tmp_path.iterdir())  # fake process never downloads


def test_cancellation_reaps_owned_process_group(tmp_path, monkeypatch):
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path / "cache"))
    monkeypatch.setattr(browser, "chromium_status", lambda: {"state": "chromium_missing"})
    monkeypatch.setattr(browser.shutil, "disk_usage", lambda _: NS(free=2**40))
    cancel = Event()
    killed = []
    waited = []
    process = NS(pid=65432, returncode=None)

    def poll():
        return None

    def wait(timeout):
        waited.append(timeout)
        return 0

    process.poll = poll
    process.wait = wait

    def start(*_a, **_kw):
        cancel.set()
        return process

    monkeypatch.setattr(browser.subprocess, "Popen", start)
    monkeypatch.setattr(browser.os, "killpg", lambda pid, sig: killed.append((pid, sig)))
    assert browser.install_chromium(consented=True, cancel=cancel) == "cancelled"
    assert killed == [(65432, browser.signal.SIGKILL)]
    assert waited == [3]


def test_custom_download_host_is_not_silently_used(tmp_path, monkeypatch):
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path / "cache"))
    monkeypatch.setenv("PLAYWRIGHT_CHROMIUM_DOWNLOAD_HOST", "https://custom.invalid")
    monkeypatch.setattr(browser, "chromium_status", lambda: {"state": "chromium_missing"})
    monkeypatch.setattr(browser.shutil, "disk_usage", lambda _: NS(free=2**40))
    monkeypatch.setattr(browser.subprocess, "Popen", lambda *_a, **_kw:
                        (_ for _ in ()).throw(AssertionError("install prohibited")))
    assert browser.install_chromium(consented=True) == "custom_download_source"


def test_low_disk_space_blocks_before_process(tmp_path, monkeypatch):
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path / "cache"))
    monkeypatch.setattr(browser, "chromium_status", lambda: {"state": "chromium_missing"})
    monkeypatch.setattr(browser.shutil, "disk_usage", lambda _: NS(free=0))
    monkeypatch.setattr(browser.subprocess, "Popen", lambda *_a, **_kw:
                        (_ for _ in ()).throw(AssertionError("install prohibited")))
    assert browser.install_chromium(consented=True) == "insufficient_space"


def test_gtk_manager_requires_explicit_confirmation_with_cancel_and_worker():
    source = (ROOT / "src/cifereca/gui/plugin_manager_page.py").read_text()
    owner = next(x for x in ast.parse(source).body
                 if isinstance(x, ast.ClassDef) and x.name == "PluginManagerPage")
    members = {x.name: ast.unparse(x) for x in owner.body
               if isinstance(x, ast.FunctionDef)}
    assert "response == 'install'" in members["_confirm_chromium_install"].replace('"', "'")
    assert "Thread(" in members["_install_chromium"]
    assert "install_chromium(consented=True, cancel=cancel)" in members["_install_chromium"]
    assert "GLib.idle_add" in members["_install_chromium"]
    assert "self._chromium_cancel.set()" in members["_cancel_chromium"]
    assert "subprocess" not in members["_install_chromium"]
    assert "pip" not in members["_install_chromium"]
