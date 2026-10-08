from __future__ import annotations

import json
from pathlib import Path
import zipfile

from cifereca.plugins import PackageStore, PluginManager, PluginStateStore


def _package(root: Path, version: str) -> Path:
    archive = root / f"plugin-{version}.zip"
    manifest = {
        "manifest": 1,
        "package": {
            "id": "org.cifereca.epub-optimizer",
            "version": version,
            "name": "Optimizer",
            "publisher": "tests",
            "plugin_api": ">=1,<2",
        },
        "plugins": [{
            "id": "device.profile.test",
            "type": "device.profile",
            "version": version,
            "entrypoint": "plugin:PLUGIN",
            "enabled_by_default": True,
        }],
    }
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("cifereca-plugin.json", json.dumps(manifest))
        bundle.writestr("plugin.py", "PLUGIN = object()\n")
    return archive


def _manager(root: Path) -> PluginManager:
    return PluginManager(
        package_store=PackageStore(root / "packages"),
        state_store=PluginStateStore(root / "state.json"),
    )


def test_upgrade_activates_expected_version_and_keeps_previous_for_rollback(tmp_path):
    manager = _manager(tmp_path)
    try:
        manager.install(_package(tmp_path, "0.2.4"))
        manager.install(_package(tmp_path, "0.3.1"))
        pointer = manager.packages.pointer("org.cifereca.epub-optimizer")
        assert pointer == {"active": "0.3.1", "previous": "0.2.4"}
        status = manager.status("device.profile.test")
        assert status.descriptor.version == "0.3.1"
        assert status.descriptor.package_version == "0.3.1"

        rolled_back = manager.rollback("org.cifereca.epub-optimizer")
        assert rolled_back == "0.2.4"
        assert manager.packages.active_version("org.cifereca.epub-optimizer") == "0.2.4"
    finally:
        manager.close()


def test_reinstall_active_version_keeps_previous_rollback_pointer(tmp_path):
    manager = _manager(tmp_path)
    try:
        manager.install(_package(tmp_path, "0.2.4"))
        archive = _package(tmp_path, "0.3.1")
        manager.install(archive)
        manager.install(archive)
        assert manager.packages.pointer("org.cifereca.epub-optimizer") == {
            "active": "0.3.1",
            "previous": "0.2.4",
        }
    finally:
        manager.close()
