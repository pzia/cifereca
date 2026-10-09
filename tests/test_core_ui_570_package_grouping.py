from __future__ import annotations

import ast
from pathlib import Path


PAGE = Path(__file__).resolve().parents[1] / "src/cifereca/gui/plugin_manager_page.py"


def _method(name: str) -> str:
    tree = ast.parse(PAGE.read_text(encoding="utf-8"))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "PluginManagerPage")
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == name)
    return ast.unparse(method)


def test_inventory_renderer_groups_installed_plugins_by_package_not_category():
    source = _method("_render_inventory")
    assert "installed_packages" in source
    assert "status.descriptor.package_id == package['package_id']" in source
    assert "for category in categories" not in source
    assert "self._append_package(package, package_plugins, statuses=statuses)" in source


def test_package_actions_live_only_in_package_card():
    package = _method("_append_package")
    plugin = _method("_plugin_row")
    assert "Revenir à" in package
    assert "Désinstaller le package" in package
    assert "_confirm_remove" in package
    assert "Revenir à" not in plugin
    assert "Désinstaller" not in plugin


def test_uninstall_confirmation_names_package_and_affected_plugins():
    source = _method("_confirm_remove")
    assert "Package :" in source
    assert "Plugins affectés" in source
    assert "package_name" in source
    assert "plugin_names" in source


def test_package_card_lists_all_contributions_as_children():
    source = _method("_append_package")
    assert "for status in package_plugins:" in source
    assert "plugin_frame.set_child(self._plugin_row(status, package, statuses=statuses))" in source
    assert "details.set_child(children)" in source
    assert "Gtk.Expander()" in source
    assert "contribution(s) · ouvrir les détails" in source
