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


def test_runtime_source_version_matches_release_file():
    import cifereca

    assert cifereca.__version__ == _release_version()
    assert cifereca.version() == _release_version()
