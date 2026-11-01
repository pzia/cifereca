"""Emergency Maintenance safety hold: plugin preferences cannot bypass unsafe tools.

The hold is a Core runtime decision, not a UI-only hide or manifest default.
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from cifereca.plugins import PackageStore, PluginManager, PluginStateStore
from cifereca.plugins.manager import _MAINTENANCE_SAFETY_HOLDS


ROOT = Path(__file__).resolve().parents[1]
AUDIT_MANIFEST = ROOT / "src/plugins/maintenance-library-audit/cifereca-plugin.json"
BUILTIN_MANIFEST = ROOT / "src/cifereca/builtin_ui/cifereca-plugin.json"
SUSPENDED = {
    "maintenance.audit.covers",
    "maintenance.audit.languages",
    "maintenance.audit.naming",
    "maintenance.audit.integrity",
    "maintenance.repair.naming",
    "maintenance.cleanup.duplicates",
    "core.maintenance.cache",
}
RECOVERY = "maintenance.cleanup.duplicates.recover"


def _package_archive(path: Path) -> Path:
    ids = sorted(SUSPENDED - {"core.maintenance.cache"}) + [RECOVERY, "test.maintenance.checked"]
    manifest = {
        "manifest": 1,
        "package": {
            "id": "org.test.maintenance-holds",
            "version": "1.0.0",
            "name": "Maintenance hold regression",
            "publisher": "tests",
            "plugin_api": ">=1,<2",
        },
        "plugins": [
            {
                "id": identifier,
                "type": "maintenance.tool",
                "entrypoint": "maintenance_test_tool:TOOL",
                "enabled_by_default": True,
            }
            for identifier in ids
        ],
    }
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("cifereca-plugin.json", json.dumps(manifest))
        archive.writestr(
            "maintenance_test_tool.py",
            "class Tool:\n"
            "    def activate(self, context): return self\n"
            "    def describe(self): return None\n"
            "    def plan(self, context): return None\n"
            "    def run(self, plan, control): return None\n"
            "TOOL = Tool()\n",
        )
    return path


def test_hold_covers_exact_shipped_unsafe_tools_and_not_recovery():
    assert set(_MAINTENANCE_SAFETY_HOLDS) == SUSPENDED
    shipped = {
        descriptor["id"]
        for path in (AUDIT_MANIFEST, BUILTIN_MANIFEST)
        for descriptor in json.loads(path.read_text(encoding="utf-8"))["plugins"]
        if descriptor["type"] == "maintenance.tool"
    }
    assert shipped == SUSPENDED | {RECOVERY}


def test_builtin_cache_tool_is_disabled_even_when_forced(tmp_path):
    manager = PluginManager(
        package_store=PackageStore(tmp_path / "packages"),
        state_store=PluginStateStore(tmp_path / "state.json"),
        builtin_ui=True,
        force_plugins=("core.maintenance.cache",),
    )
    try:
        manager.activate()
        status = manager.status("core.maintenance.cache")
        assert not status.enabled
        assert status.state == "disabled"
        assert status.reason_code == "maintenance_safety_hold"
        assert status.reason_detail == {"issue": "#3996"}
        assert manager.service("core.maintenance.cache") is None
        with pytest.raises(ValueError, match="temporarily disabled for safety"):
            manager.set_enabled("core.maintenance.cache", True)
        assert manager.service("core.maintenance.cache") is None
    finally:
        manager.close()


def test_stale_preferences_and_force_cannot_activate_held_installed_tools(tmp_path):
    state = PluginStateStore(tmp_path / "state.json")
    for identifier in SUSPENDED:
        state.set_enabled(identifier, True)

    manager = PluginManager(
        package_store=PackageStore(tmp_path / "packages"),
        state_store=state,
        builtin_ui=True,
        force_plugins=tuple(sorted(SUSPENDED)),
    )
    try:
        manager.install(_package_archive(tmp_path / "tools.zip"))
        manager.activate()

        for identifier in sorted(SUSPENDED):
            status = manager.status(identifier)
            assert not status.enabled
            assert status.state == "disabled"
            assert status.reason_code == "maintenance_safety_hold"
            assert status.reason_detail["issue"] == _MAINTENANCE_SAFETY_HOLDS[identifier]
            assert manager.service(identifier) is None
            with pytest.raises(ValueError, match="temporarily disabled for safety"):
                manager.set_enabled(identifier, True)

        active = {row.plugin_id for row in manager.services("maintenance.tool")}
        assert active == {RECOVERY, "test.maintenance.checked"}
        assert manager.status(RECOVERY).enabled
        assert manager.status("test.maintenance.checked").enabled

        # Refresh and saved-state replay must not re-expose a suspended tool.
        manager.refresh()
        manager.activate()
        assert {row.plugin_id for row in manager.services("maintenance.tool")} == active
        assert not any(manager.status(identifier).enabled for identifier in SUSPENDED)
    finally:
        manager.close()
