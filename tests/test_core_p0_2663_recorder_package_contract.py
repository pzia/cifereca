"""Headless release-ZIP contract for installed Browser Recorder diagnostics.

No GTK, Playwright, network, accounts or private capture; complements the
real GTK installed-ZIP tests, which run on the dedicated GTK runner only.
"""
from __future__ import annotations

import json
from pathlib import Path
from zipfile import ZipFile

from scripts.plugin_packaging import build_plugin_zip, validate_plugin_zip


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src/plugins/browser-recorder"
PACKAGE = "org.cifereca.technical.browser-recorder"
PLUGIN = "ui.page.browser-recorder"


def test_release_zip_contains_scoped_recorder_and_explicit_browser_dependency(
    tmp_path,
):
    """A headless contract exercises the exact archive used by GTK E2E."""
    archive = build_plugin_zip(SOURCE, tmp_path)
    validated = validate_plugin_zip(archive)
    assert validated["package"]["id"] == PACKAGE
    assert validated["package"]["version"] == "0.2.7"

    with ZipFile(archive) as bundle:
        names = set(bundle.namelist())
        assert {
            "cifereca-plugin.json",
            "browser_recorder_page.py",
            "browser_recorder_review_ui.py",
            "browser_recorder_http.py",
        } <= names
        manifest = json.loads(bundle.read("cifereca-plugin.json"))
        page_source = bundle.read("browser_recorder_page.py").decode("utf-8")
        private_ui = bundle.read("browser_recorder_review_ui.py").decode("utf-8")
        assert manifest["package"]["id"] == PACKAGE
        assert len(manifest["plugins"]) == 1
        page = manifest["plugins"][0]
        assert page["id"] == PLUGIN
        assert page["enabled_by_default"] is False
        assert page["requires_brokers"] == ["tasks"]
        assert {
            "type": "python-package",
            "name": "playwright",
            "version": ">=1.50,<2",
            "import": "playwright",
        } in page["system_requires"]
        # The installed Python sources, not an unbundled checkout, carry
        # the scoped Core callback and closed operation identifiers.
        assert "report_diagnostic" in page_source
        assert '"http_capture"' in private_ui
        assert '"zip_export"' in private_ui
        assert all(not name.startswith("tests/") for name in names)
        assert not any(
            name.endswith((".so", ".exe", ".dll")) for name in names
        )
