from __future__ import annotations

from pathlib import Path

import cifereca
from cifereca.plugins.manager import _load_builtin_package


ROOT = Path(__file__).resolve().parents[1]


def test_source_runtime_version_tracks_release_version_file():
    assert cifereca.__version__ == (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    assert cifereca.version() == cifereca.__version__


def test_runtime_version_falls_back_to_installed_distribution_metadata(tmp_path):
    source = tmp_path / "site-packages" / "cifereca" / "__init__.py"
    source.parent.mkdir(parents=True)
    source.write_text("", encoding="utf-8")

    assert cifereca._resolve_version(
        source_file=source,
        metadata_version=lambda name: "9.8.7",
    ) == "9.8.7"


def test_source_version_wins_over_unrelated_installed_metadata(tmp_path):
    source = tmp_path / "repo" / "src" / "cifereca" / "__init__.py"
    source.parent.mkdir(parents=True)
    source.write_text("", encoding="utf-8")
    (tmp_path / "repo" / "VERSION").write_text("1.2.3\n", encoding="utf-8")

    assert cifereca._resolve_version(
        source_file=source,
        metadata_version=lambda name: "9.8.7",
    ) == "1.2.3"


def test_builtin_ui_runtime_manifest_is_normalized_to_core_version():
    package = _load_builtin_package(ROOT / "src" / "cifereca" / "builtin_ui")

    assert package.manifest.package.version == cifereca.__version__
    assert package.sha256 == "builtin"
    assert package.manifest.plugins
    assert all(plugin.version == cifereca.__version__ for plugin in package.manifest.plugins)
    assert all(plugin.package_version == cifereca.__version__ for plugin in package.manifest.plugins)
