"""P1 #957: no retired youth source can be packaged, installed or reactivated.

The historical per-identity Youth cache is deliberately left untouched.
"""
from __future__ import annotations

import ast
import json
import zipfile

import pytest
from pathlib import Path

from cifereca.plugins.manager import _RETIRED_YOUTH_SOURCE_PACKAGES
from cifereca.plugins.manager import PluginManager
from cifereca.plugins import PackageStore, PluginStateStore

ROOT = Path(__file__).resolve().parents[1]
PLUGINS = ROOT / "src/plugins"
RETIRED = {
    "org.cifereca.youth-source-usborne",
    "org.cifereca.youth-source-booktrust",
    "org.cifereca.youth-catalog",
    "org.cifereca.youth-bibliography-openlibrary",
    "org.cifereca.youth-detail-enrichment",
    "org.cifereca.youth-source-discovery",
    "org.cifereca.youth-source-ecole-loisirs",
    "org.cifereca.youth-source-milan",
}


def test_retired_sources_have_no_shipped_manifests():
    assert RETIRED == _RETIRED_YOUTH_SOURCE_PACKAGES
    for package_id in RETIRED:
        path = PLUGINS / package_id.removeprefix("org.cifereca.")
        assert not (path / "cifereca-plugin.json").exists()


def test_retired_package_store_is_excluded_from_runtime_discovery():
    tree = ast.parse((ROOT / "src/cifereca/plugins/manager.py").read_text())
    manager = next(node for node in tree.body
                   if isinstance(node, ast.ClassDef) and node.name == "PluginManager")
    method = next(node for node in manager.body
                  if isinstance(node, ast.FunctionDef) and node.name == "refresh")
    source = ast.unparse(method)
    assert "if package_id in _RETIRED_YOUTH_SOURCE_PACKAGES:" in source
    assert source.index("if package_id in _RETIRED_YOUTH_SOURCE_PACKAGES:") < source.index(
        "self.packages.load(package_id)")
    install = next(node for node in manager.body
                   if isinstance(node, ast.FunctionDef) and node.name == "install")
    install_source = ast.unparse(install)
    assert "if package_id in _RETIRED_YOUTH_SOURCE_PACKAGES:" in install_source
    assert "raise ValueError" in install_source


def test_youth_source_inventory_and_ci_mapping_exclude_retired_packages():
    dependencies = json.loads(
        (ROOT / ".github/plugin-test-dependencies.json").read_text())
    assert all("booktrust" not in p and "usborne" not in p
               for p in dependencies["audited_product_packages"])
    for package, deps in dependencies["dependencies"].items():
        assert "booktrust" not in package and "usborne" not in package
        assert all("booktrust" not in p and "usborne" not in p for p in deps)


@pytest.mark.parametrize("package_id", sorted(RETIRED))
def test_old_installed_package_cannot_reactivate_and_cache_is_left_intact(tmp_path, package_id):
    # Simulate a pre-upgrade store with a retired active package. The
    # underlying files remain available for user-controlled cleanup.
    store = PackageStore(tmp_path / "packages")
    root = store.version_root(package_id, "0.2.0")
    root.mkdir(parents=True)
    (root / "historical-cache.marker").write_text("preserve")
    store.set_active(package_id, "0.2.0")
    manager = PluginManager(
        package_store=store,
        state_store=PluginStateStore(tmp_path / "state.json"),
    )
    try:
        manager.refresh()
        assert not [row for row in manager.diagnostic()
                    if row.get("package_id") == package_id]
        assert store.active_version(package_id) == "0.2.0"
        assert (root / "historical-cache.marker").read_text() == "preserve"
    finally:
        manager.close()


@pytest.mark.parametrize("package_id", sorted(RETIRED))
def test_retired_plugin_reinstallation_is_refused(tmp_path, package_id):
    archive = tmp_path / "old-provider.zip"
    manifest = {
        "manifest": 1,
        "package": {
            "id": package_id,
            "version": "0.2.0",
            "name": "Retired old provider",
            "publisher": "tests",
            "plugin_api": ">=1,<2",
        },
        "plugins": [{
            "id": "youth.source.old-example",
            "type": "transport",
            "entrypoint": "retired_source:PLUGIN",
        }],
    }
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("cifereca-plugin.json", json.dumps(manifest))
        bundle.writestr("retired_source.py", "PLUGIN = object()")
    store = PackageStore(tmp_path / "packages")
    manager = PluginManager(
        package_store=store,
        state_store=PluginStateStore(tmp_path / "state.json"),
    )
    try:
        with pytest.raises(ValueError, match="retired youth provider"):
            manager.install(archive)
        assert package_id not in store.all_packages()
    finally:
        manager.close()
