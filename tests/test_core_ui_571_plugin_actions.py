from __future__ import annotations

import ast
from pathlib import Path


PAGE = Path(__file__).resolve().parents[1] / "src/cifereca/gui/plugin_manager_page.py"


def _method(name: str) -> str:
    tree = ast.parse(PAGE.read_text(encoding="utf-8"))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "PluginManagerPage")
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == name)
    return ast.unparse(method)


def test_package_secondary_actions_use_hamburger_menu():
    source = _method("_append_package")
    assert "package_menu.set_icon_name('open-menu-symbolic')" in source
    assert "Revenir à" in source
    assert "Désinstaller le package" in source
    assert "package_menu_box.append(rollback)" in source
    assert "package_menu_box.append(remove)" in source


def test_plugin_keeps_configure_visible_and_diagnostic_secondary():
    source = _method("_plugin_row")
    assert "primary_actions.append(cfg)" in source
    assert "secondary.set_icon_name('open-menu-symbolic')" in source
    assert "secondary_box.append(diag)" in source
    assert "primary_actions.append(diag)" not in source


def test_priority_and_activation_are_visibly_named():
    source = _method("_plugin_row")
    assert "Gtk.Label(label='Priorité'" in source
    assert "Ordre de préférence de ce plugin" in source
    assert "Gtk.Label(label='Activé'" in source
    assert "Activer ou désactiver" in source


def test_identity_control_stays_available_without_competing_with_settings():
    source = _method("_plugin_row")
    assert "Gtk.MenuButton(label='Identités…')" in source
    assert "primary_actions.append(identities)" in source
    assert "settings.append(priority_group)" in source
    assert "settings.append(activation_group)" in source
