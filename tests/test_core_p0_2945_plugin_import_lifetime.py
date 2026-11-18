"""Lifecycle regression for package-owned imports; not a Python sandbox."""
from pathlib import Path
import json
import sys
import zipfile

import pytest

from cifereca.plugins import PackageStore, PluginManager, PluginStateStore


def _archive(tmp_path, name, version, marker, *, fail=False, sibling=False):
    package_id = f"org.test.lifetime.{name}"
    module = f"lifetime_{name}_entry"
    helper = f"lifetime_{name}_helper"
    plugins = [{"id": f"lifetime.{name}", "type": "recommendation",
                "entrypoint": f"{module}:Plugin"}]
    if sibling:
        plugins.append({"id": f"lifetime.{name}.sibling", "type": "recommendation",
                        "entrypoint": f"{module}_sibling:Plugin"})
    manifest = {"manifest": 1, "package": {"id": package_id, "name": name,
                    "version": version, "plugin_api": ">=1,<2"}, "plugins": plugins}
    archive = tmp_path / f"{name}-{version}.zip"
    code = (f"from {helper} import MARKER\n"
            "class Plugin:\n"
            " def activate(self, ctx): return self\n"
            " def value(self): return MARKER\n"
            " def late(self):\n"
            f"  from {helper} import MARKER\n"
            "  return MARKER\n")
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("cifereca-plugin.json", json.dumps(manifest))
        z.writestr(f"{helper}.py", f"MARKER = {marker!r}\n")
        z.writestr(f"{module}.py", code + ("raise RuntimeError('synthetic')\n" if fail else ""))
        if sibling:
            z.writestr(f"{module}_sibling.py", code)
    return archive, package_id, module, helper


@pytest.fixture
def manager(tmp_path):
    m = PluginManager(package_store=PackageStore(tmp_path / "packages"),
                      state_store=PluginStateStore(tmp_path / "state.json"))
    try:
        yield m
    finally:
        m.close()


def test_version_switch_drops_old_helpers_and_preserves_unrelated_instance(tmp_path, manager):
    a1, aid, module, helper = _archive(tmp_path, "aa", "1.0", "old")
    a2, _, _, _ = _archive(tmp_path, "aa", "2.0", "new")
    b, bid, bm, bh = _archive(tmp_path, "bb", "1.0", "other")
    first = manager.install(a1)
    manager.install(b)
    kept = manager.service("lifetime.bb")
    assert manager.service("lifetime.aa").late() == "old"
    manager.install(a2)
    assert manager.service("lifetime.aa").value() == "new"
    assert manager.service("lifetime.aa").late() == "new"
    assert manager.service("lifetime.bb") is kept
    assert kept.late() == "other"
    assert str(first.root) not in sys.path
    assert Path(sys.modules[helper].__file__).parent == manager.packages.version_root(aid, "2.0")
    manager.rollback(aid)
    assert manager.service("lifetime.aa").late() == "old"


def test_disable_last_sibling_retires_root_without_touching_live_sibling(tmp_path, manager):
    archive, aid, module, helper = _archive(tmp_path, "siblings", "1.0", "shared", sibling=True)
    package = manager.install(archive)
    sibling = manager.service("lifetime.siblings.sibling")
    assert sibling.late() == "shared"
    manager.set_enabled("lifetime.siblings", False)
    assert manager.service("lifetime.siblings.sibling") is sibling
    assert str(package.root) in sys.path
    assert sibling.late() == "shared"
    manager.set_enabled("lifetime.siblings.sibling", False)
    assert str(package.root) not in sys.path
    assert all(name not in sys.modules for name in (module, module + "_sibling", helper))
    manager.set_enabled("lifetime.siblings", True)
    assert manager.service("lifetime.siblings").late() == "shared"


def test_failed_import_does_not_leave_its_helper_or_path(tmp_path, manager):
    archive, aid, module, helper = _archive(tmp_path, "bad", "1.0", "bad", fail=True)
    package = manager.install(archive)
    assert manager.status("lifetime.bad").state == "error"
    assert manager.service("lifetime.bad") is None
    assert str(package.root) not in sys.path
    assert module not in sys.modules and helper not in sys.modules


def test_same_version_replacement_reimports_helpers(tmp_path, manager):
    archive, aid, module, helper = _archive(tmp_path, "replace", "1.0", "one")
    manager.install(archive)
    assert manager.service("lifetime.replace").late() == "one"
    archive, _, _, _ = _archive(tmp_path, "replace", "1.0", "two")
    manager.install(archive, replace=True)
    assert manager.service("lifetime.replace").late() == "two"
