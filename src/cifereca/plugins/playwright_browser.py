"""Core-owned, opt-in Playwright headed Chromium provisioning (#2628).

There is NO downloader on plugin discovery, page rendering or activation.
Only install_chromium(consent=True) is allowed to spawn the official Playwright
installer. No sudo/apt, other browsers, shell, HAR, capture or user-data logs.
Never expose executable paths, subprocess output, proxy values or exceptions.
"""
from __future__ import annotations

from dataclasses import dataclass
import importlib.metadata
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import sys
from threading import Event, Lock
import time
from typing import Callable

from packaging.specifiers import SpecifierSet

_INSTALL_LOCK = Lock()
_MIN_AVAILABLE_BYTES = 768 * 1024 * 1024
_MAX_INSTALL_SECONDS = 900
_MAX_VERSION_CHARS = 40


@dataclass(frozen=True, slots=True)
class BrowserState:
    code: str
    version: str = ""

    def redacted(self) -> dict[str, object]:
        # Do not include disk paths, CLI output, account/proxy details.
        return {"code": self.code, "playwright_version": self.version,
                "browser": "chromium", "read_only": True}


def _version() -> str | None:
    try:
        version = importlib.metadata.version("playwright")
        if version not in SpecifierSet(">=1.50,<2"):
            return None
        if len(version) > _MAX_VERSION_CHARS or not version.replace(".", "").replace("-", "").isalnum():
            return None
        return version
    except (importlib.metadata.PackageNotFoundError, ValueError, TypeError):
        return None


def _cache_path() -> Path:
    """Only the user's own cache. Reject relative/foreign/env '0' paths."""
    home = Path.home()
    raw = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if raw is not None:
        if not raw or raw == "0":
            raise ValueError("browser_cache_unavailable")
        cache = Path(raw)
    else:
        xdg = os.environ.get("XDG_CACHE_HOME")
        base = Path(xdg) if xdg else home / ".cache"
        cache = base / "ms-playwright"
    if (not home.is_absolute() or not cache.is_absolute()
            or ".." in cache.parts or cache == home or not cache.is_relative_to(home)):
        raise ValueError("browser_cache_unavailable")
    # A cache symlink could redirect a downloader outside the authorized home.
    for item in (home, *list(cache.parents)[::-1], cache):
        if item == Path("/"):
            continue
        try:
            value = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(value.st_mode):
            raise ValueError("browser_cache_unavailable")
        if item == cache and not stat.S_ISDIR(value.st_mode):
            raise ValueError("browser_cache_unavailable")
    # A cache can be inside HOME yet unwritable (read-only mount, ACL or
    # inaccessible parent). Fail before spawning the official downloader;
    # Playwright must not fall back to a different directory silently.
    existing = next((p for p in (cache, *cache.parents) if p.exists()), None)
    if existing is None or not existing.is_dir():
        raise ValueError("browser_cache_unavailable")
    if not os.access(existing, os.W_OK | os.X_OK):
        raise ValueError("browser_cache_unavailable")
    return cache


def _executable() -> Path:
    # This starts Playwright's *local driver* only, not Chromium, any browser
    # navigation, HTTP request or download. Use its version-coupled path rather
    # than guessing Chromium revisions, formats or custom cache layouts.
    from playwright.sync_api import sync_playwright
    with sync_playwright() as driver:
        return Path(driver.chromium.executable_path)


def inspect_chromium(*, test_launch: bool = False) -> BrowserState:
    """Explicit offline check. Launch smoke test ONLY if user requested it."""
    version = _version()
    if version is None:
        return BrowserState("playwright_missing")
    if not sys.platform.startswith("linux"):
        return BrowserState("unsupported_platform", version)
    try:
        cache = _cache_path()
        exe = _executable()
        # Reject symlinks anywhere between the cache root and executable;
        # a replaced revision directory must not redirect a probe/launch.
        relative_parts = exe.relative_to(cache).parts if exe.is_relative_to(cache) else ()
        ancestor = cache
        unsafe = not relative_parts
        for part in relative_parts:
            ancestor = ancestor / part
            if ancestor.is_symlink():
                unsafe = True
                break
        if unsafe:
            return BrowserState("browser_cache_unavailable", version)
        if (not exe.is_file() or not os.access(exe, os.X_OK)):
            # Detect a cached older revision without claiming compatibility.
            if cache.is_dir() and any(
                row.name.startswith("chromium-") for row in cache.iterdir()
            ):
                return BrowserState("chromium_revision_incompatible", version)
            return BrowserState("chromium_missing", version)
        if test_launch:
            from playwright.sync_api import sync_playwright
            try:
                with sync_playwright() as driver:
                    browser = driver.chromium.launch(headless=True, channel="chromium", timeout=10000)
                    browser.close()
            except Exception as exc:
                # Classification only; never return the driver's raw message.
                raw = str(exc).lower()
                if ("host system is missing dependencies" in raw
                        or "error while loading shared libraries" in raw
                        or "shared libraries are missing" in raw):
                    return BrowserState("linux_libraries_missing", version)
                return BrowserState("browser_launch_failed", version)
            return BrowserState("ready", version)
        return BrowserState("chromium_installed_unverified", version)
    except ValueError:
        return BrowserState("browser_cache_unavailable", version)
    except Exception:
        return BrowserState("browser_probe_failed", version)


def _stop(proc: subprocess.Popen) -> None:
    # Terminate the entire installer process group (incl. its Node downloader).
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (OSError, AttributeError):
        try:
            proc.terminate()
        except OSError:
            pass
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        pass

    # Reaping the Playwright CLI parent does NOT prove its downloader
    # subprocesses have exited. A child can ignore SIGTERM while the Node
    # launcher exits promptly. Check the original dedicated process group
    # even when proc.wait() already returned, and force-stop remaining members.
    # Every installer is launched with start_new_session=True above.
    try:
        os.killpg(proc.pid, 0)
    except ProcessLookupError:
        # Entire process group is gone; do not signal a reused process ID.
        pass
    except (OSError, AttributeError):
        # Unsupported group signalling: at least ensure the direct child is
        # stopped. Never target the process group of our own application.
        if proc.poll() is None:
            try:
                proc.kill()
            except OSError:
                pass
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (OSError, AttributeError):
            if proc.poll() is None:
                try:
                    proc.kill()
                except OSError:
                    pass

    try:
        proc.wait(timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        pass


def install_chromium(
    *, consent: bool = False, cancelled: Event | None = None,
    progress: Callable[[str], None] | None = None, timeout: int = _MAX_INSTALL_SECONDS,
) -> BrowserState:
    """Opt-in only. Never installs OS dependencies, other browsers or pip.

    The official Playwright installer selects its coupled Chromium revision
    and verifies its own downloads. Its output is discarded, never logged.
    We leave pre-existing revisions untouched (--no-remove). Failure does
    not authorize deleting any shared browser cache.
    """
    if consent is not True:
        return BrowserState("consent_required")
    if type(timeout) is not int or not 1 <= timeout <= _MAX_INSTALL_SECONDS:
        return BrowserState("invalid_install_options")
    if cancelled is not None and cancelled.is_set():
        return BrowserState("cancelled")
    if not _INSTALL_LOCK.acquire(blocking=False):
        return BrowserState("installation_already_running")
    try:
        before = inspect_chromium()
        if before.code not in {
            "chromium_missing", "chromium_revision_incompatible",
            "chromium_installed_unverified",
        }:
            return before
        if before.code == "chromium_installed_unverified":
            return inspect_chromium(test_launch=True)
        if (sys.prefix == getattr(sys, "base_prefix", sys.prefix)
                or not Path(sys.executable).is_file()):
            return BrowserState("venv_required", before.version)
        try:
            cache = _cache_path()
            target = next((p for p in (cache, *cache.parents) if p.is_dir()), None)
            if target is None or shutil.disk_usage(target).free < _MIN_AVAILABLE_BYTES:
                return BrowserState("disk_space_insufficient", before.version)
        except (OSError, ValueError):
            return BrowserState("browser_cache_unavailable", before.version)
        # A provider-controlled environment must not silently replace the
        # official browser archive source claimed in the user consent dialog.
        # Normal HTTPS proxies remain supported; custom mirrors require a
        # separate, explicitly reviewed administrative workflow.
        if any(os.environ.get(key) for key in (
            "PLAYWRIGHT_DOWNLOAD_HOST", "PLAYWRIGHT_CHROMIUM_DOWNLOAD_HOST",
        )):
            return BrowserState("browser_download_source_unapproved", before.version)
        if progress is not None:
            progress("starting")
        try:
            # No shell, sudo, install-deps or pip. No raw output in diagnostics.
            # --no-remove protects browsers used by other Playwright clients.
            proc = subprocess.Popen(
                [sys.executable, "-m", "playwright", "install",
                 "--no-shell", "--no-remove", "chromium"],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, close_fds=True, start_new_session=True,
            )
        except OSError:
            return BrowserState("installer_unavailable", before.version)
        started = time.monotonic()
        try:
            while True:
                if cancelled is not None and cancelled.is_set():
                    _stop(proc)
                    return BrowserState("cancelled", before.version)
                if time.monotonic() - started >= timeout:
                    _stop(proc)
                    return BrowserState("download_timeout", before.version)
                code = proc.poll()
                if code is not None:
                    if code != 0:
                        # A failed CLI may have exited before its worker
                        # downloader subprocesses. Reap the *entire* dedicated
                        # group even when the parent already has an exit code.
                        _stop(proc)
                        return BrowserState("download_failed", before.version)
                    break
                time.sleep(0.2)
        finally:
            # Reap if the process exited normally; _stop handles the rest.
            if proc.poll() is not None:
                proc.wait()
        if progress is not None:
            progress("verifying")
        after = inspect_chromium(test_launch=True)
        if after.code in {"chromium_missing", "chromium_revision_incompatible"}:
            return BrowserState("download_incomplete", before.version)
        return after
    finally:
        _INSTALL_LOCK.release()


__all__ = ["BrowserState", "inspect_chromium", "install_chromium"]
