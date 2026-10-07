from __future__ import annotations

import json
from pathlib import Path
import zipfile

from cifereca.plugins import PackageStore, PluginManager, PluginStateStore


def _manager(tmp_path: Path) -> PluginManager:
    return PluginManager(
        package_store=PackageStore(tmp_path / "packages"),
        state_store=PluginStateStore(tmp_path / "plugins-state.json"),
    )


def _package(tmp_path: Path, version: str, marker: str) -> Path:
    archive = tmp_path / f"versioned-{version}.zip"
    manifest = {
        "manifest": 1,
        "package": {
            "id": "org.test.versioned",
            "version": version,
            "name": "Versioned",
            "publisher": "tests",
            "plugin_api": ">=1,<2",
        },
        "plugins": [{
            "id": "test.versioned",
            "type": "transport",
            "entrypoint": "versioned_plugin:PLUGIN",
        }],
    }
    source = (
        "class P:\n"
        f" def activate(self, ctx): return {marker!r}\n"
        " def deactivate(self): pass\n"
        "PLUGIN=P()\n"
    )
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("cifereca-plugin.json", json.dumps(manifest))
        bundle.writestr("versioned_plugin.py", source)
    return archive


def test_package_inventory_tracks_active_previous_and_full_remove(tmp_path: Path):
    manager = _manager(tmp_path / "state")
    try:
        manager.install(_package(tmp_path, "1.0.0", "one"))
        manager.install(_package(tmp_path, "1.1.0", "two"))

        inventory = manager.package_inventory()
        assert len(inventory) == 1
        assert inventory[0]["package_id"] == "org.test.versioned"
        assert inventory[0]["active"] == "1.1.0"
        assert inventory[0]["previous"] == "1.0.0"
        assert inventory[0]["versions"] == ["1.1.0", "1.0.0"]

        assert manager.rollback("org.test.versioned") == "1.0.0"
        inventory = manager.package_inventory()
        assert inventory[0]["active"] == "1.0.0"
        assert inventory[0]["previous"] == "1.1.0"

        manager.remove_package("org.test.versioned")

        assert manager.package_inventory() == []
        assert manager.statuses() == []
        assert not manager.packages.package_root("org.test.versioned").exists()
    finally:
        manager.close()
