from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def _package(tmp_path: Path) -> Path:
    archive = tmp_path / "cli-plugin.zip"
    manifest = {
        "manifest": 1,
        "package": {
            "id": "org.test.cli",
            "version": "1.0.0",
            "name": "CLI test",
            "publisher": "tests",
            "plugin_api": ">=1,<2",
        },
        "plugins": [{
            "id": "test.cli",
            "type": "recommendation",
            "entrypoint": "cli_plugin:PLUGIN",
        }],
    }
    source = (
        "class P:\n"
        " def activate(self, ctx): return self\n"
        " def deactivate(self): pass\n"
        "PLUGIN=P()\n"
    )
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("cifereca-plugin.json", json.dumps(manifest))
        bundle.writestr("cli_plugin.py", source)
    return archive


def _run(env: dict[str, str], *args: str) -> object:
    completed = subprocess.run(
        [sys.executable, "-m", "cifereca.cli", "plugin", *args],
        cwd=ROOT,
        env=env,
        check=True,
        text=True,
        capture_output=True,
    )
    return json.loads(completed.stdout)


def test_cli_plugin_commands_share_persistent_plugin_runtime(tmp_path: Path):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    env["XDG_CONFIG_HOME"] = str(tmp_path / "config")
    env["XDG_DATA_HOME"] = str(tmp_path / "data")
    env["XDG_CACHE_HOME"] = str(tmp_path / "cache")
    env["XDG_STATE_HOME"] = str(tmp_path / "state")

    assert all(row["plugin_id"] != "test.cli" for row in _run(env, "list"))

    installed = _run(env, "install", str(_package(tmp_path)))
    assert installed["package_id"] == "org.test.cli"
    assert installed["active"] == "1.0.0"
    assert installed["plugins"] == ["test.cli"]

    listed = _run(env, "list")
    row = next(row for row in listed if row["plugin_id"] == "test.cli")
    assert row["state"] == "ready"
    assert row["enabled"] is True

    disabled = _run(env, "disable", "test.cli")
    assert disabled["plugin_id"] == "test.cli"
    assert disabled["state"] == "disabled"
    assert disabled["enabled"] is False

    packages = _run(env, "packages")
    package = next(row for row in packages if row["package_id"] == "org.test.cli")
    assert package["active"] == "1.0.0"
    assert package["versions"] == ["1.0.0"]
