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


def test_blocked_dependency_prevents_dependent_import(tmp_path: Path, monkeypatch):
    sentinel = tmp_path / "DEPENDENT_IMPORTED"
    archive = tmp_path / "blocked-dependency.zip"
    manifest = {
        "manifest": 1,
        "package": {
            "id": "org.test.blocked-dependency",
            "version": "1.0.0",
            "name": "Blocked dependency",
            "publisher": "tests",
            "plugin_api": ">=1,<2",
        },
        "plugins": [
            {
                "id": "test.runtime",
                "type": "runtime.adapter",
                "entrypoint": "runtime_plugin:PLUGIN",
                "system_requires": [{
                    "type": "binary",
                    "name": "cifereca-binary-that-does-not-exist",
                }],
            },
            {
                "id": "test.consumer",
                "type": "drm.handler",
                "entrypoint": "consumer_plugin:PLUGIN",
                "requires": [{"plugin": "test.runtime"}],
            },
        ],
    }

    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("cifereca-plugin.json", json.dumps(manifest))
        bundle.writestr(
            "runtime_plugin.py",
            "raise RuntimeError('blocked runtime must not import')\n",
        )
        bundle.writestr(
            "consumer_plugin.py",
            (
                "from pathlib import Path\n"
                f"Path({str(sentinel)!r}).write_text('bad')\n"
                "class P:\n"
                " def activate(self, ctx): return self\n"
                " def deactivate(self): pass\n"
                "PLUGIN=P()\n"
            ),
        )

    monkeypatch.setenv("PATH", str(tmp_path))
    manager = _manager(tmp_path / "state")
    try:
        manager.install(archive)

        runtime = manager.status("test.runtime")
        consumer = manager.status("test.consumer")

        assert runtime.state == "blocked"
        assert runtime.reason == "system_requirement"
        assert consumer.state == "blocked"
        assert consumer.reason == "dependency_unavailable"
        assert not sentinel.exists()
        assert manager.service("test.consumer") is None
    finally:
        manager.close()
