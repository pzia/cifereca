from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from cifereca.plugin_api import SystemRequirement
from cifereca.plugins.manifest import parse_manifest
from cifereca.plugins.packages import PackageStore
from cifereca.plugins.manager import PluginManager
from cifereca.plugins.requirements import check_system_requirement
from cifereca.plugins.python_dependencies import PythonDependencyError, PythonDependencyTransaction
from cifereca.plugins.state import PluginStateStore


def _manifest(requirement: dict | None = None) -> dict:
    plugin = {
        "id": "test.python-dependency",
        "type": "test.service",
        "version": "1.0.0",
        "entrypoint": "dependency_plugin:PLUGIN",
    }
    if requirement is not None:
        plugin["system_requires"] = [requirement]
    return {
        "manifest": 1,
        "package": {
            "id": "org.test.python-dependency",
            "version": "1.0.0",
            "name": "python dep test",
            "publisher": "tests",
            "plugin_api": ">=1,<2",
        },
        "categories": [{
            "id": "test.service",
            "display_name": "Test service",
            "contract": "test.service/1",
            "resolution": "all",
        }],
        "plugins": [plugin],
    }


def _archive(path: Path, manifest: dict, source: str) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("cifereca-plugin.json", json.dumps(manifest))
        z.writestr("dependency_plugin.py", source)
    return path


def test_legacy_named_python_requirement_is_normalized_to_distribution():
    manifest = parse_manifest(_manifest({"type": "python", "name": "Pillow", "version": ">=10"}))
    requirement = manifest.plugins[0].system_requires[0]
    assert requirement.type == "python-package"
    assert requirement.name == "Pillow"
    assert requirement.version == ">=10"


def test_explicit_python_package_accepts_import_probe():
    manifest = parse_manifest(_manifest({"type": "python-package", "name": "Pillow", "version": ">=10", "import": "PIL"}))
    requirement = manifest.plugins[0].system_requires[0]
    assert requirement.type == "python-package"
    assert requirement.import_name == "PIL"
    status = check_system_requirement(requirement)
    assert status.ok
    assert status.detected


def test_python_interpreter_requirement_keeps_existing_semantics():
    manifest = parse_manifest(_manifest({"type": "python", "version": ">=3.11"}))
    requirement = manifest.plugins[0].system_requires[0]
    assert requirement.type == "python"
    assert requirement.name == ""
    assert check_system_requirement(requirement).ok


def test_missing_distribution_is_reported_without_importing_plugin():
    requirement = SystemRequirement(type="python-package", name="cifereca-package-that-does-not-exist-xyz")
    status = check_system_requirement(requirement)
    assert not status.ok
    assert "not installed" in status.message


class _Txn:
    def __init__(self):
        self.committed = False
        self.rolled_back = False

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


class _FakeDeps:
    def __init__(self, sentinel: Path, *, fail: bool = False):
        self.sentinel = sentinel
        self.fail = fail
        self.calls = []
        self.txn = _Txn()

    def ensure(self, manifests, *, package_roots=()):
        self.calls.append((tuple(manifests), tuple(package_roots)))
        if self.fail:
            raise PythonDependencyError("resolver failed")
        self.sentinel.write_text("resolved", encoding="utf-8")
        return self.txn

    def diagnostic(self, manifests):
        return []


def _manager(tmp_path: Path, deps) -> PluginManager:
    return PluginManager(
        package_store=PackageStore(tmp_path / "packages"),
        state_store=PluginStateStore(tmp_path / "state.json"),
        python_dependencies=deps,
    )


def test_core_resolves_python_dependencies_before_entrypoint_import(tmp_path):
    sentinel = tmp_path / "deps-ready"
    imported = tmp_path / "imported"
    archive = _archive(
        tmp_path / "plugin.zip",
        _manifest({"type": "python-package", "name": "Pillow"}),
        (
            "from pathlib import Path\n"
            f"assert Path({str(sentinel)!r}).is_file(), 'dependency resolution must happen before import'\n"
            f"Path({str(imported)!r}).write_text('yes')\n"
            "class P:\n"
            " def activate(self, ctx): return 'ready'\n"
            " def deactivate(self): pass\n"
            "PLUGIN=P()\n"
        ),
    )
    deps = _FakeDeps(sentinel)
    manager = _manager(tmp_path, deps)
    manager.install(archive)
    assert deps.calls
    assert deps.txn.committed
    assert imported.is_file()
    assert manager.service("test.python-dependency") == "ready"
    manager.close()


def test_dependency_failure_does_not_activate_or_import_plugin(tmp_path):
    sentinel = tmp_path / "deps-ready"
    imported = tmp_path / "imported"
    archive = _archive(
        tmp_path / "plugin.zip",
        _manifest({"type": "python-package", "name": "does-not-matter"}),
        f"from pathlib import Path\nPath({str(imported)!r}).write_text('bad')\nPLUGIN=object()\n",
    )
    deps = _FakeDeps(sentinel, fail=True)
    manager = _manager(tmp_path, deps)
    with pytest.raises(PythonDependencyError, match="resolver failed"):
        manager.install(archive)
    assert not imported.exists()
    assert manager.packages.active_version("org.test.python-dependency") is None
    assert manager.packages.installed_versions("org.test.python-dependency") == []
    manager.close()
