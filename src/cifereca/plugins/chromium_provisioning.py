"""Core-owned opt-in Chromium install for Browser Recorder (#2628).

No browser fetch, shell, root package manager or filesystem writes on import
or status inspection. All externally visible statuses are closed error codes.
"""
from __future__ import annotations

import importlib.metadata
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import time
from threading import Event

MIN_FREE_BYTES = 750 * 1024 * 1024
INSTALL_TIMEOUT = 600
PROBE_TIMEOUT = 12

_PROBE_CODE = """import pathlib
try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        print("ready" if pathlib.Path(p.chromium.executable_path).is_file()
              else "missing")
except Exception:
    print("unavailable")
"""


def _python_version() -> str:
    try:
        value = importlib.metadata.version("playwright")
        return value if len(value) < 40 else ""
    except Exception:
        return ""


def chromium_status() -> dict[str, object]:
    """Side-effect-free, offline probe; never returns executable/cache paths."""
    version = _python_version()
    if not version:
        return {"state": "playwright_missing", "version": "",
                "native_libraries": "unverified"}
    if not sys.platform.startswith("linux"):
        return {"state": "platform_unsupported", "version": version,
                "native_libraries": "unverified"}
    try:
        result = subprocess.run(
            [sys.executable, "-c", _PROBE_CODE], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=PROBE_TIMEOUT, check=False, text=True,
        )
        if result.returncode == 0 and result.stdout.strip() == "ready":
            state = "ready"
        elif result.returncode == 0 and result.stdout.strip() == "missing":
            state = "chromium_missing"
        else:
            state = "probe_failed"
    except (OSError, subprocess.TimeoutExpired):
        state = "probe_failed"
    # Executable existence does NOT prove native libraries or display support.
    return {"state": state, "version": version, "native_libraries": "unverified"}


def _cache_location() -> Path | None:
    """Do not follow symlinks or install into untrusted/relative cache paths."""
    raw = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if raw:
        if raw == "0":
            return None  # would mutate package directory instead of OS cache
        path = Path(raw)
        if not path.is_absolute():
            return None
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache")))
        if not base.is_absolute():
            return None
        path = base / "ms-playwright"
    for item in (path, *path.parents):
        try:
            if item.is_symlink():
                return None
        except OSError:
            return None
    return path


def _stop(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        try:
            process.kill()
        except OSError:
            pass
    try:
        process.wait(timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        pass


def install_chromium(*, consented: bool = False,
                     cancel: Event | None = None,
                     timeout: int = INSTALL_TIMEOUT) -> str:
    """Install only headed Chromium after explicit UI consent.

    No sudo, --with-deps, removal of previous revisions, secrets or CLI logs.
    """
    if consented is not True:
        return "consent_required"
    if cancel is not None and cancel.is_set():
        return "cancelled"
    state = chromium_status()["state"]
    if state == "ready":
        return "already_ready"
    if state != "chromium_missing":
        return str(state)
    if not sys.platform.startswith("linux") or os.name != "posix":
        return "platform_unsupported"
    dest = _cache_location()
    if dest is None:
        return "unsafe_cache_path"
    existing = next((p for p in (dest, *dest.parents) if p.exists()), None)
    if existing is None:
        return "unsafe_cache_path"
    try:
        if shutil.disk_usage(existing).free < MIN_FREE_BYTES:
            return "insufficient_space"
    except OSError:
        return "storage_unavailable"
    if type(timeout) is not int or not 1 <= timeout <= INSTALL_TIMEOUT:
        return "invalid_timeout"
    # The managed installer advertises the official Playwright distribution,
    # so do not silently honor an alternate browser-archive download host.
    # Proxy settings are still supported (without ever rendering their values).
    if any(os.environ.get(key) for key in (
        "PLAYWRIGHT_DOWNLOAD_HOST", "PLAYWRIGHT_CHROMIUM_DOWNLOAD_HOST",
    )):
        return "custom_download_source"
    env = os.environ.copy()
    env["PLAYWRIGHT_SKIP_BROWSER_GC"] = "1"
    command = [sys.executable, "-m", "playwright", "install",
               "--no-shell", "--no-remove", "chromium"]
    process = None
    started = time.monotonic()
    try:
        process = subprocess.Popen(
            command, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            close_fds=True, start_new_session=True,
        )
        while process.poll() is None:
            if cancel is not None and cancel.is_set():
                return "cancelled"
            if time.monotonic() - started > timeout:
                return "download_timeout"
            time.sleep(.1)
        if process.returncode != 0:
            return "download_failed"
    except OSError:
        return "download_failed"
    finally:
        if process is not None:
            _stop(process)
    if cancel is not None and cancel.is_set():
        return "cancelled"
    return "ready" if chromium_status()["state"] == "ready" else "revision_incompatible"
