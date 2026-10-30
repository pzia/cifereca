from __future__ import annotations
from packaging.version import Version

import json
import sys
import zipfile
from pathlib import Path

import pytest

from cifereca import __version__
from cifereca.core import CiferecaCore
from cifereca.plugin_api import PLUGIN_API_VERSION
from cifereca.plugins import PackageInstallError, PackageInstaller, PackageStore, PluginManager, PluginStateStore


def _manifest(package_id: str, version: str, plugins: list[dict], *, categories: list[dict] | None = None) -> dict:
    return {
        "manifest": 1,
        "package": {
            "id": package_id,
            "version": version,
            "name": package_id,
            "publisher": "tests",
            "plugin_api": ">=1,<2",
        },
        "categories": categories or [],
        "plugins": plugins,
    }




def _untyped_service_manifest(package_id: str, version: str, plugins: list[dict]) -> dict:
    normalized = [dict(plugin, type="test.service") for plugin in plugins]
    return _manifest(
        package_id,
        version,
        normalized,
        categories=[{
            "id": "test.service",
            "display_name": "Test service",
            "contract": "test.service/1",
            "resolution": "all",
        }],
    )

def _zip(path: Path, manifest: dict, files: dict[str, str] | None = None) -> Path:
    with zipfile.ZipFile(path, "w") as bundle:
        bundle.writestr("cifereca-plugin.json", json.dumps(manifest))
        for name, content in (files or {}).items():
            bundle.writestr(name, content)
    return path


def _manager(tmp_path: Path) -> PluginManager:
    return PluginManager(
        package_store=PackageStore(tmp_path / "packages"),
        state_store=PluginStateStore(tmp_path / "plugins-state.json"),
    )


def test_step_b_public_api_and_core_categories_are_versioned(tmp_path):
    assert Version(__version__) >= Version("0.9.2")
    assert PLUGIN_API_VERSION == "1.4.3"
    manager = _manager(tmp_path)
    categories = {row.id for row in manager.categories.all()}
    assert {
        "provider.library", "transport", "drm.handler", "runtime.adapter",
        "storage.target", "ui.page", "ui.widgets", "meta.bundle",
    }.issubset(categories)


def test_step_b_install_is_manifest_first_and_does_not_import_code(tmp_path):
    sentinel = tmp_path / "IMPORTED"
    archive = _zip(
        tmp_path / "plugin.zip",
        _manifest("org.test.manifest", "1.0.0", [{
            "id": "test.manifest", "type": "transport", "entrypoint": "manifest_plugin:PLUGIN",
        }]),
        {"manifest_plugin.py": f"from pathlib import Path\nPath({str(sentinel)!r}).write_text('bad')\nPLUGIN=object()\n"},
    )
    store = PackageStore(tmp_path / "packages")
    installer = PackageInstaller(store)
    inspected = installer.inspect(archive)
    assert inspected.package.id == "org.test.manifest"
    installed = installer.install(archive, activate=False)
    assert installed.manifest.package.version == "1.0.0"
    assert not sentinel.exists()


def test_step_b_safe_zip_rejects_path_traversal(tmp_path):
    archive = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("../escape.py", "x=1")
        bundle.writestr("cifereca-plugin.json", json.dumps(_manifest(
            "org.test.evil", "1.0.0",
            [{"id": "test.evil", "type": "transport", "entrypoint": "evil:PLUGIN"}],
        )))
    with pytest.raises(PackageInstallError, match="unsafe plugin archive path"):
        PackageInstaller(PackageStore(tmp_path / "packages")).inspect(archive)


def test_step_b_custom_category_is_registered_before_plugin_validation(tmp_path):
    archive = _zip(
        tmp_path / "custom.zip",
        _manifest(
            "org.test.custom", "1.0.0",
            [{"id": "test.custom", "type": "metadata.enricher", "entrypoint": "custom_plugin:PLUGIN"}],
            categories=[{
                "id": "metadata.enricher", "display_name": "Metadata", "contract": "example.metadata/1", "resolution": "all",
            }],
        ),
        {"custom_plugin.py": "class P:\n def activate(self, ctx): return 'ok'\n def deactivate(self): pass\nPLUGIN=P()\n"},
    )
    manager = _manager(tmp_path)
    manager.install(archive)
    assert manager.categories.get("metadata.enricher").contract == "example.metadata/1"
    assert manager.status("test.custom").state == "ready"
    assert manager.service("test.custom") == "ok"
    manager.close()


def test_step_b_dependencies_activate_in_order_and_services_are_brokered(tmp_path):
    dep = _zip(
        tmp_path / "dep.zip",
        _manifest("org.test.dep", "1.0.0", [{
            "id": "test.dep", "type": "runtime.adapter", "entrypoint": "dep_plugin:PLUGIN",
        }]),
        {"dep_plugin.py": "class P:\n def activate(self, ctx): return {'runtime': 'ready'}\n def deactivate(self): pass\nPLUGIN=P()\n"},
    )
    child = _zip(
        tmp_path / "child.zip",
        _manifest("org.test.child", "1.0.0", [{
            "id": "test.child", "type": "drm.handler", "entrypoint": "child_plugin:PLUGIN",
            "requires": [{"plugin": "test.dep", "version": ">=1,<2"}],
        }]),
        {"child_plugin.py": "class P:\n def activate(self, ctx): return {'dependency': ctx.service('test.dep')}\n def deactivate(self): pass\nPLUGIN=P()\n"},
    )
    manager = _manager(tmp_path)
    manager.install(dep)
    manager.install(child)
    assert manager.status("test.dep").state == "ready"
    assert manager.status("test.child").state == "ready"
    assert manager.service("test.child") == {"dependency": {"runtime": "ready"}}
    manager.close()


def test_step_b_missing_dependency_and_cycle_are_blocked_without_crashing(tmp_path):
    missing = _zip(
        tmp_path / "missing.zip",
        _manifest("org.test.missing", "1.0.0", [{
            "id": "test.missing", "type": "drm.handler", "entrypoint": "missing_plugin:PLUGIN",
            "requires": [{"plugin": "test.nowhere"}],
        }]),
        {"missing_plugin.py": "PLUGIN=object()\n"},
    )
    manager = _manager(tmp_path)
    manager.install(missing)
    status = manager.status("test.missing")
    assert status.state == "blocked" and status.reason == "dependency_missing"

    cycle = _zip(
        tmp_path / "cycle.zip",
        _manifest("org.test.cycle", "1.0.0", [
            {"id": "test.a", "type": "transport", "entrypoint": "a_plugin:PLUGIN", "requires": [{"plugin": "test.b"}]},
            {"id": "test.b", "type": "transport", "entrypoint": "b_plugin:PLUGIN", "requires": [{"plugin": "test.a"}]},
        ]),
        {"a_plugin.py": "PLUGIN=object()\n", "b_plugin.py": "PLUGIN=object()\n"},
    )
    manager.install(cycle)
    assert manager.status("test.a").reason == "dependency_cycle"
    assert manager.status("test.b").reason == "dependency_cycle"
    manager.close()


def test_step_b_system_requirements_are_reported_as_blocked(tmp_path):
    archive = _zip(
        tmp_path / "requirements.zip",
        _manifest("org.test.requirements", "1.0.0", [
            {
                "id": "test.python", "type": "runtime.adapter", "entrypoint": "ok_plugin:PLUGIN",
                "system_requires": [{"type": "python", "version": ">=3.11"}],
            },
            {
                "id": "test.binary", "type": "runtime.adapter", "entrypoint": "bad_plugin:PLUGIN",
                "system_requires": [{"type": "binary", "name": "cifereca-binary-that-does-not-exist"}],
            },
        ]),
        {
            "ok_plugin.py": "class P:\n def activate(self,ctx): return 'ok'\n def deactivate(self): pass\nPLUGIN=P()\n",
            "bad_plugin.py": "raise RuntimeError('must not import')\n",
        },
    )
    manager = _manager(tmp_path)
    manager.install(archive)
    assert manager.status("test.python").state == "ready"
    blocked = manager.status("test.binary")
    assert blocked.state == "blocked"
    assert blocked.reason == "system_requirement"
    assert "binary not found" in blocked.error
    manager.close()


def test_step_b_priorities_select_highest_ready_plugin_and_user_override(tmp_path):
    archive = _zip(
        tmp_path / "storage.zip",
        _manifest("org.test.storage", "1.0.0", [
            {"id": "storage.basic-test", "type": "storage.target", "entrypoint": "basic_plugin:PLUGIN", "priority": 10},
            {"id": "storage.special-test", "type": "storage.target", "entrypoint": "special_plugin:PLUGIN", "priority": 100},
        ]),
        {
            "basic_plugin.py": "class P:\n def activate(self,ctx): return 'basic'\n def deactivate(self): pass\nPLUGIN=P()\n",
            "special_plugin.py": "class P:\n def activate(self,ctx): return 'special'\n def deactivate(self): pass\nPLUGIN=P()\n",
        },
    )
    manager = _manager(tmp_path)
    manager.install(archive)
    assert manager.select("storage.target").descriptor.id == "storage.special-test"
    manager.set_priority("storage.basic-test", 200)
    manager.activate()
    assert manager.select("storage.target").descriptor.id == "storage.basic-test"
    manager.close()


def test_step_b_disabling_dependency_blocks_dependent_and_reenabling_recovers(tmp_path):
    archive = _zip(
        tmp_path / "deps.zip",
        _manifest("org.test.deps", "1.0.0", [
            {"id": "runtime.test", "type": "runtime.adapter", "entrypoint": "runtime_plugin:PLUGIN"},
            {"id": "drm.test", "type": "drm.handler", "entrypoint": "drm_plugin:PLUGIN", "requires": [{"plugin": "runtime.test"}]},
        ]),
        {
            "runtime_plugin.py": "class P:\n def activate(self,ctx): return 'runtime'\n def deactivate(self): pass\nPLUGIN=P()\n",
            "drm_plugin.py": "class P:\n def activate(self,ctx): return 'drm'\n def deactivate(self): pass\nPLUGIN=P()\n",
        },
    )
    manager = _manager(tmp_path)
    manager.install(archive)
    assert manager.status("drm.test").state == "ready"
    manager.set_enabled("runtime.test", False)
    assert manager.status("runtime.test").state == "disabled"
    assert manager.status("drm.test").state == "blocked"
    assert manager.status("drm.test").reason == "dependency_disabled"
    manager.set_enabled("runtime.test", True)
    assert manager.status("runtime.test").state == "ready"
    assert manager.status("drm.test").state == "ready"
    manager.close()


def test_step_b_activation_error_is_isolated_from_other_plugins(tmp_path):
    archive = _zip(
        tmp_path / "isolation.zip",
        _untyped_service_manifest("org.test.isolation", "1.0.0", [
            {"id": "test.broken", "entrypoint": "broken_plugin:PLUGIN"},
            {"id": "test.good", "entrypoint": "good_plugin:PLUGIN"},
        ]),
        {
            "broken_plugin.py": "raise RuntimeError('boom')\n",
            "good_plugin.py": "class P:\n def activate(self,ctx): return 'good'\n def deactivate(self): pass\nPLUGIN=P()\n",
        },
    )
    manager = _manager(tmp_path)
    manager.install(archive)
    assert manager.status("test.broken").state == "error"
    assert "boom" in manager.status("test.broken").error
    assert manager.status("test.good").state == "ready"
    assert manager.service("test.good") == "good"
    manager.close()


def test_step_b_package_versions_upgrade_and_rollback_reload_code(tmp_path):
    manager = _manager(tmp_path)
    v1 = _zip(
        tmp_path / "v1.zip",
        _untyped_service_manifest("org.test.versioned", "1.0.0", [{"id": "test.versioned", "entrypoint": "versioned_plugin:PLUGIN"}]),
        {"versioned_plugin.py": "class P:\n def activate(self,ctx): return 'v1'\n def deactivate(self): pass\nPLUGIN=P()\n"},
    )
    v2 = _zip(
        tmp_path / "v2.zip",
        _untyped_service_manifest("org.test.versioned", "2.0.0", [{"id": "test.versioned", "entrypoint": "versioned_plugin:PLUGIN"}]),
        {"versioned_plugin.py": "class P:\n def activate(self,ctx): return 'v2'\n def deactivate(self): pass\nPLUGIN=P()\n"},
    )
    manager.install(v1)
    assert manager.service("test.versioned") == "v1"
    manager.install(v2)
    assert manager.packages.active_version("org.test.versioned") == "2.0.0"
    assert manager.service("test.versioned") == "v2"
    assert manager.rollback("org.test.versioned") == "1.0.0"
    assert manager.service("test.versioned") == "v1"
    manager.close()


def test_step_b_same_package_version_with_different_hash_is_rejected(tmp_path):
    store = PackageStore(tmp_path / "packages")
    installer = PackageInstaller(store)
    manifest = _manifest("org.test.hash", "1.0.0", [{"id": "test.hash", "type": "transport", "entrypoint": "hash_plugin:PLUGIN"}])
    first = _zip(tmp_path / "first.zip", manifest, {"hash_plugin.py": "PLUGIN=object()\n"})
    second = _zip(tmp_path / "second.zip", manifest, {"hash_plugin.py": "PLUGIN='different'\n"})
    installer.install(first)
    with pytest.raises(PackageInstallError, match="different SHA-256"):
        installer.install(second)


def test_step_b_meta_plugin_can_bundle_dependencies_without_code(tmp_path):
    archive = _zip(
        tmp_path / "meta.zip",
        _manifest("org.test.meta", "1.0.0", [
            {"id": "test.component", "type": "test.service", "entrypoint": "component:PLUGIN"},
            {"id": "meta.test-suite", "type": "meta.bundle", "requires": [{"plugin": "test.component"}]},
        ], categories=[{"id": "test.service", "display_name": "Test service", "contract": "test.service/1", "resolution": "all"}]),
        {"component.py": "class P:\n def activate(self,ctx): return 'component'\n def deactivate(self): pass\nPLUGIN=P()\n"},
    )
    manager = _manager(tmp_path)
    manager.install(archive)
    assert manager.status("meta.test-suite").state == "ready"
    assert manager.service("meta.test-suite") is None
    manager.close()


def test_step_b_core_composition_owns_plugin_manager_in_isolated_paths(tmp_path):
    with CiferecaCore.open(config_path=tmp_path / "config.json", database_path=tmp_path / "library.sqlite3") as core:
        assert isinstance(core.plugins, PluginManager)
        assert core.plugins.packages.root == tmp_path / "plugins" / "packages"
        assert core.plugins.state.path == tmp_path / "plugins-state.json"


def test_step_b_recommends_are_non_blocking_but_conflicts_only_block_when_enabled(tmp_path):
    archive = _zip(
        tmp_path / "relations.zip",
        _manifest("org.test.relations", "1.0.0", [
            {
                "id": "test.primary", "type": "test.service", "entrypoint": "primary:PLUGIN",
                "recommends": [{"plugin": "test.optional"}],
                "conflicts": [{"plugin": "test.conflict"}],
            },
            {"id": "test.conflict", "type": "test.service", "entrypoint": "conflict:PLUGIN", "enabled_by_default": False},
        ], categories=[{"id": "test.service", "display_name": "Test service", "contract": "test.service/1", "resolution": "all"}]),
        {
            "primary.py": "class P:\n def activate(self,ctx): return 'primary'\n def deactivate(self): pass\nPLUGIN=P()\n",
            "conflict.py": "class P:\n def activate(self,ctx): return 'conflict'\n def deactivate(self): pass\nPLUGIN=P()\n",
        },
    )
    manager = _manager(tmp_path)
    manager.install(archive)
    assert manager.status("test.primary").state == "ready"
    assert manager.status("test.conflict").state == "disabled"
    manager.set_enabled("test.conflict", True)
    assert manager.status("test.primary").state == "blocked"
    assert manager.status("test.primary").reason == "conflict"
    manager.close()


def test_step_b_incompatible_plugin_api_is_rejected_before_install(tmp_path):
    manifest = _manifest(
        "org.test.future", "1.0.0",
        [{"id": "test.future", "type": "transport", "entrypoint": "future:PLUGIN"}],
    )
    manifest["package"]["plugin_api"] = ">=2,<3"
    archive = _zip(tmp_path / "future.zip", manifest, {"future.py": "PLUGIN=object()\n"})
    installer = PackageInstaller(PackageStore(tmp_path / "packages"))
    with pytest.raises(Exception, match="requires Plugin API"):
        installer.inspect(archive)


def test_step_b_installed_package_can_remain_inactive_until_explicit_activation(tmp_path):
    archive = _zip(
        tmp_path / "dormant.zip",
        _manifest("org.test.dormant", "1.0.0", [{
            "id": "test.dormant", "type": "transport", "entrypoint": "dormant:PLUGIN",
        }]),
        {"dormant.py": "raise RuntimeError('must stay dormant')\n"},
    )
    manager = _manager(tmp_path)
    installed = manager.install(archive, activate=False)
    assert installed.manifest.package.version == "1.0.0"
    assert manager.packages.installed_versions("org.test.dormant") == ["1.0.0"]
    assert manager.packages.active_version("org.test.dormant") is None
    assert manager.statuses() == []
    manager.packages.set_active("org.test.dormant", "1.0.0")
    manager.activate()
    assert manager.status("test.dormant").state == "error"
    manager.close()
