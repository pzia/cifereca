"""P0 #2628 — real Core-owned Chromium provisioning in a fresh Linux venv.

CI invokes this ONLY after explicitly opting in via the runner environment.
No credentials, provider URLs, HAR, archive or browser profile are collected.
Unlike the normal offline synthetic tests, this exercise downloads real
Playwright Chromium into a disposable, initially empty user cache.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys

import pytest

from cifereca.plugins.playwright_browser import (
    inspect_chromium, install_chromium,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("CIFERECA_REAL_CHROMIUM_PROVISION_E2E") != "1",
    reason="requires explicit CI opt-in for network Chromium installation",
)


def test_empty_cache_consent_gate_then_real_chromium_download_and_launch():
    assert sys.platform.startswith("linux")
    assert sys.prefix != sys.base_prefix, "Core must run inside a managed venv"
    home = Path.home()
    assert home.is_dir()
    assert home.name == "empty-browser-home"
    assert os.environ.get("PLAYWRIGHT_BROWSERS_PATH") is None
    assert os.environ.get("PLAYWRIGHT_DOWNLOAD_HOST") is None
    assert os.environ.get("PLAYWRIGHT_CHROMIUM_DOWNLOAD_HOST") is None

    cache = home / ".cache" / "ms-playwright"
    assert not cache.exists()  # runner owns its disposable, fresh HOME
    initial = inspect_chromium()
    assert initial.code == "chromium_missing"
    assert initial.version

    # Discovery/check and missing consent must NEVER download or mutate cache.
    assert inspect_chromium().code == "chromium_missing"
    assert install_chromium(consent=False).code == "consent_required"
    assert not cache.exists()

    phases = []
    done = install_chromium(consent=True, progress=phases.append, timeout=840)
    assert done.code == "ready", f"redacted state: {done.code}"
    assert phases == ["starting", "verifying"]
    assert done.version == initial.version

    # The Core checks the version-coupled binary AND launches it, not merely
    # whether a directory exists. Only headed Chromium was provisioned.
    assert inspect_chromium(test_launch=True).code == "ready"
    folders = tuple(x.name for x in cache.iterdir())
    assert any(name.startswith("chromium-") for name in folders)
    assert not any(name.startswith(("chromium_headless_shell-", "firefox-", "webkit-"))
                   for name in folders)

    # Repeating the explicit install must be a no-op: no second download.
    second_phases = []
    repeat = install_chromium(consent=True, progress=second_phases.append)
    assert repeat.code == "ready"
    assert second_phases == []

    # Never dump stdout/stderr, browser paths, proxy environment or user data.
    redacted = repeat.redacted()
    assert set(redacted) == {"code", "playwright_version", "browser", "read_only"}
    assert redacted["browser"] == "chromium"
    assert "ready" == redacted["code"]
