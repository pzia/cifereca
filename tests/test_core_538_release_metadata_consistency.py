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


def test_source_plugin_manifests_have_unique_ids_and_metadata():
    """Manifest integrity is independent of the next release snapshot."""
    seen = set()
    for path in sorted((ROOT / "src/plugins").glob("*/cifereca-plugin.json")):
        package = json.loads(path.read_text(encoding="utf-8"))["package"]
        package_id = package["id"]
        assert isinstance(package_id, str) and package_id
        assert package_id not in seen, f"Duplicate plugin package: {package_id}"
        seen.add(package_id)
        for key in ("name", "version"):
            assert isinstance(package[key], str) and package[key], (path, key)


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
