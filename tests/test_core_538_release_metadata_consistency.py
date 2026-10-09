from __future__ import annotations

import json
from pathlib import Path
import tomllib


ROOT = Path(__file__).resolve().parents[1]


def _release_version() -> str:
    return (ROOT / "VERSION").read_text(encoding="utf-8").strip()


def test_source_release_metadata_share_one_version():
    expected = _release_version()

    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["version"] == expected

    builtin = json.loads(
        (ROOT / "src" / "cifereca" / "builtin_ui" / "cifereca-plugin.json").read_text(encoding="utf-8")
    )
    assert builtin["package"]["version"] == expected
    assert builtin["plugins"]
    assert all(plugin["version"] == expected for plugin in builtin["plugins"])

    plugin_release = json.loads(
        (ROOT / "src" / "cifereca" / "plugin-release.json").read_text(encoding="utf-8")
    )
    assert plugin_release["release"] == expected


def test_release_inventory_matches_all_source_plugin_manifests():
    """Prevent an incomplete Core release after adding or updating a plugin."""
    release = json.loads(
        (ROOT / "src" / "cifereca" / "plugin-release.json").read_text(encoding="utf-8")
    )
    actual = {}
    for manifest_path in sorted((ROOT / "src" / "plugins").glob("*/cifereca-plugin.json")):
        package = json.loads(manifest_path.read_text(encoding="utf-8"))["package"]
        package_id = str(package["id"])
        assert package_id not in actual, f"Duplicate plugin package: {package_id}"
        actual[package_id] = {
            "version": str(package["version"]),
            "name": str(package["name"]),
        }
    assert release["packages"] == actual, (
        "Core release inventory must include every source package at its actual "
        "version and name; synchronize plugin-release.json before the next release"
    )


def test_core_release_exposes_strict_plugins_only_cli(capsys):
    """The released Core must ship the updater contract used by official plugin ZIPs."""
    import pytest
    from cifereca.dev_updater import main

    with pytest.raises(SystemExit) as exit_info:
        main(["--help"])
    assert exit_info.value.code == 0
    help_text = capsys.readouterr().out
    assert "--require-plugins-only" in help_text
    assert "--expected-sha256" in help_text


def test_runtime_source_version_matches_release_file():
    import cifereca

    assert cifereca.__version__ == _release_version()
    assert cifereca.version() == _release_version()
