"""Lifecycle regression for package-owned imports; not a Python sandbox."""
from pathlib import Path
import json
import sys
import zipfile

import pytest

from cifereca.plugins import PackageStore, PluginManager, PluginStateStore


def _archive(tmp_path, name, version, marker, *, fail=False, sibling=False,
             helper_name=None, helper_package=False, namespace=False):
    package_id = f"org.test.lifetime.{name}"
    module = f"lifetime_{name}_entry"
    helper = helper_name or f"lifetime_{name}_helper"
    import_name = helper + '.leaf' if namespace else helper
    plugins = [{"id": f"lifetime.{name}", "type": "recommendation",
                "entrypoint": f"{module}:Plugin"}]
    if sibling:
        plugins.append({"id": f"lifetime.{name}.sibling", "type": "recommendation",
                        "entrypoint": f"{module}_sibling:Plugin"})
    manifest = {"manifest": 1, "package": {"id": package_id, "name": name,
                    "version": version, "plugin_api": ">=1,<2"}, "plugins": plugins}
    archive = tmp_path / f"{name}-{version}.zip"
    code = (f"from {import_name} import MARKER\n"
            "class Plugin:\n"
            " def activate(self, ctx): return self\n"
            " def value(self): return MARKER\n"
            " def late(self):\n"
            f"  from {import_name} import MARKER\n"
            "  return MARKER\n")
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("cifereca-plugin.json", json.dumps(manifest))
        path = (f"{helper}/leaf.py" if namespace else
                f"{helper}/__init__.py" if helper_package else f"{helper}.py")
        z.writestr(path, f"MARKER = {marker!r}\n")
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


@pytest.mark.parametrize("order", [("aa", "bb"), ("bb", "aa")])
@pytest.mark.parametrize("package_form", [False, True])
def test_homonymous_helpers_are_refused_symmetrically_and_recover(
    tmp_path, manager, order, package_form,
):
    helper = "cifereca_test_homonymous_helper"
    archives = {
        name: _archive(tmp_path, name, "1.0", name, helper_name=helper,
                       helper_package=package_form and name == "bb")[0]
        for name in order
    }
    first, second = order
    manager.install(archives[first])
    assert manager.service(f"lifetime.{first}").late() == first
    manager.install(archives[second])
    for name in order:
        status = manager.status(f"lifetime.{name}")
        assert status.state == "blocked"
        assert status.reason_code == "import_namespace_conflict"
        assert manager.service(f"lifetime.{name}") is None
    assert helper not in sys.modules
    manager.set_enabled(f"lifetime.{first}", False)
    assert manager.service(f"lifetime.{second}").value() == second
    assert manager.service(f"lifetime.{second}").late() == second
    manager.set_enabled(f"lifetime.{second}", False)
    manager.set_enabled(f"lifetime.{first}", True)
    assert manager.service(f"lifetime.{first}").value() == first
    assert manager.service(f"lifetime.{first}").late() == first


def test_reserved_namespace_cannot_shadow_stdlib(tmp_path, manager):
    archive, _, module, _ = _archive(
        tmp_path, "reserved", "1.0", "bad", helper_name="json",
    )
    original = sys.modules["json"]
    manager.install(archive)
    assert manager.status("lifetime.reserved").reason_code == "import_namespace_conflict"
    assert manager.service("lifetime.reserved") is None
    assert sys.modules["json"] is original
    assert module not in sys.modules


def test_foreign_cache_is_not_evicted_or_used(tmp_path, manager, monkeypatch):
    from types import ModuleType
    helper = "cifereca_test_foreign_cached"
    foreign = ModuleType(helper)
    foreign.__file__ = str(tmp_path / "foreign" / (helper + ".py"))
    foreign.MARKER = "wrong"
    monkeypatch.setitem(sys.modules, helper, foreign)
    archive, _, module, _ = _archive(
        tmp_path, "cached", "1.0", "right", helper_name=helper,
    )
    manager.install(archive)
    assert manager.status("lifetime.cached").state == "error"
    assert manager.service("lifetime.cached") is None
    assert sys.modules[helper] is foreign
    assert module not in sys.modules


def test_namespace_directory_claims_code_without_init(tmp_path):
    from cifereca.plugins.loader import package_namespace_conflicts
    roots = {name: tmp_path / name for name in ("a", "b")}
    for root in roots.values():
        (root / "fixture_namespace").mkdir(parents=True)
        (root / "fixture_namespace" / "helper.py").write_text("VALUE = 1")
        (root / "assets").mkdir()
    assert package_namespace_conflicts(roots) == {
        "a": ("fixture_namespace",), "b": ("fixture_namespace",),
    }


def test_pep420_namespace_import_retirement_and_rollback(tmp_path, manager):
    a1, aid, module, helper = _archive(tmp_path, "ns", "1.0", "old", namespace=True)
    a2, _, _, _ = _archive(tmp_path, "ns", "2.0", "new", namespace=True)
    manager.install(a1)
    assert manager.service("lifetime.ns").late() == "old"
    assert helper in sys.modules and helper + ".leaf" in sys.modules
    manager.set_enabled("lifetime.ns", False)
    assert all(name not in sys.modules for name in (module, helper, helper + ".leaf"))
    manager.set_enabled("lifetime.ns", True)
    assert manager.service("lifetime.ns").late() == "old"
    manager.install(a2)
    assert manager.service("lifetime.ns").late() == "new"
    manager.rollback(aid)
    assert manager.service("lifetime.ns").late() == "old"


def test_mixed_namespace_is_not_evicted_by_retirement(tmp_path, manager, monkeypatch):
    from types import ModuleType
    archive, _, _, _ = _archive(tmp_path, "mixed", "1.0", "local")
    installed = manager.install(archive)
    name = "cifereca_test_mixed_namespace"
    mixed = ModuleType(name)
    mixed.__path__ = [str(installed.root / name), str(tmp_path / "foreign" / name)]
    monkeypatch.setitem(sys.modules, name, mixed)
    manager.set_enabled("lifetime.mixed", False)
    assert sys.modules[name] is mixed
