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


def _archive(tmp_path: Path, *, package_id: str, plugin_id: str, module_name: str, source: str) -> Path:
    archive = tmp_path / f"{package_id}.zip"
    manifest = {
        "manifest": 1,
        "package": {
            "id": package_id,
            "version": "1.0.0",
            "name": package_id,
            "publisher": "tests",
            "plugin_api": ">=1,<2",
        },
        "plugins": [{
            "id": plugin_id,
            "type": "recommendation",
            "entrypoint": f"{module_name}:PLUGIN",
        }],
    }
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("cifereca-plugin.json", json.dumps(manifest))
        bundle.writestr(f"{module_name}.py", source)
    return archive


def test_installing_unrelated_plugin_preserves_existing_live_service(tmp_path: Path):
    first = _archive(
        tmp_path,
        package_id="org.test.first",
        plugin_id="test.first",
        module_name="first_plugin",
        source=(
            "class P:\n"
            " def __init__(self): self.activations=0; self.deactivations=0\n"
            " def activate(self, ctx): self.activations += 1; return self\n"
            " def deactivate(self): self.deactivations += 1\n"
            "PLUGIN=P()\n"
        ),
    )
    second = _archive(
        tmp_path,
        package_id="org.test.second",
        plugin_id="test.second",
        module_name="second_plugin",
        source=(
            "class P:\n"
            " def activate(self, ctx): return self\n"
            " def deactivate(self): pass\n"
            "PLUGIN=P()\n"
        ),
    )

    manager = _manager(tmp_path / "state")
    try:
        manager.install(first)
        service = manager.service("test.first")
        assert service.activations == 1
        assert service.deactivations == 0

        manager.install(second)

        assert manager.service("test.first") is service
        assert service.activations == 1
        assert service.deactivations == 0
        assert manager.status("test.first").state == "ready"
        assert manager.status("test.second").state == "ready"
    finally:
        manager.close()
