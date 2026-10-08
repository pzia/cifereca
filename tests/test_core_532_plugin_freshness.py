from __future__ import annotations

import json
from pathlib import Path

from cifereca.plugin_release import (
    classify_package_freshness,
    expected_plugin_packages,
    plugin_release_manifest,
)
from cifereca.plugins import PackageStore, PluginManager, PluginStateStore
from cifereca.system_status import system_status


ROOT = Path(__file__).resolve().parents[1]


def test_release_manifest_matches_current_snapshot_and_plugin_manifests():
    release = plugin_release_manifest()
    assert release["release"] == (ROOT / "VERSION").read_text(encoding="utf-8").strip()

    actual = {}
    for manifest_path in sorted((ROOT / "src" / "plugins").glob("*/cifereca-plugin.json")):
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        package = raw["package"]
        actual[str(package["id"])] = str(package["version"])

    expected = {
        package_id: row["version"]
        for package_id, row in expected_plugin_packages().items()
    }
    assert expected == actual


def test_freshness_classifier_covers_canonical_states():
    assert classify_package_freshness("1.2.0", "1.2.0", ["1.2.0"]) == ("up_to_date", "")
    assert classify_package_freshness("1.1.0", "1.2.0", ["1.1.0"]) == ("outdated", "older_than_expected")
    assert classify_package_freshness("1.3.0", "1.2.0", ["1.3.0"]) == ("newer_nonstandard", "newer_than_expected")
    assert classify_package_freshness(None, "1.2.0", []) == ("unknown", "not_installed")
    assert classify_package_freshness(None, "1.2.0", ["1.2.0"]) == ("unknown", "inactive")
    assert classify_package_freshness("1.0.0", None, ["1.0.0"]) == ("unknown", "unmanaged")


def test_inventory_can_expose_expected_but_missing_release_packages(tmp_path):
    manager = PluginManager(
        package_store=PackageStore(tmp_path / "packages"),
        state_store=PluginStateStore(tmp_path / "state.json"),
    )
    try:
        assert manager.package_inventory() == []
        rows = manager.package_inventory(include_expected=True)
        paris = next(row for row in rows if row["package_id"] == "org.cifereca.provider.paris")
        assert paris["versions"] == []
        assert paris["active"] is None
        assert paris["expected_version"] == expected_plugin_packages()["org.cifereca.provider.paris"]["version"]
        assert paris["freshness"] == "unknown"
        assert paris["freshness_reason"] == "not_installed"
    finally:
        manager.close()


def test_system_status_keeps_expected_missing_packages_visible():
    class Manager:
        def diagnostic(self):
            return []

        def package_inventory(self, *, include_expected=False):
            assert include_expected is True
            return [{
                "package_id": "org.cifereca.provider.paris",
                "active": None,
                "versions": [],
                "expected_version": "1.2.20",
                "freshness": "unknown",
                "freshness_reason": "not_installed",
                "builtin": False,
            }]

    status = system_status(Manager())
    assert status["packages"][0]["expected_version"] == "1.2.20"
    assert status["packages"][0]["freshness_reason"] == "not_installed"


def test_plugin_manager_ui_consumes_core_freshness_projection():
    source = (ROOT / "src" / "cifereca" / "gui" / "plugin_manager_page.py").read_text(encoding="utf-8")
    assert "package_inventory(include_expected=True)" in source
    assert "Packages attendus absents" in source
    assert "plus récente / non standard" in source
    assert "Version : ancienne" in source
