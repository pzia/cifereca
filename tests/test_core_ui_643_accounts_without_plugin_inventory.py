from __future__ import annotations

import ast
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[1] / "src/cifereca/gui/main_window.py"


def _settings_source() -> str:
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "MainWindow")
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == "_build_settings_page")
    return ast.unparse(method)


def test_accounts_tab_starts_with_identity_configuration_not_plugin_inventory():
    source = _settings_source()
    assert "Plugins fournisseurs" not in source
    assert "descriptor.capabilities.names()" not in source
    assert "Plugin fournisseur" not in source
    identities = source.index("identities_title = Gtk.Label(label='Identités'")
    accounts = source.index("accounts_title = Gtk.Label(label='Sources et comptes fournisseurs'")
    assert identities < accounts


def test_provider_descriptors_still_drive_account_editors_without_exposing_capability_codes():
    source = _settings_source()
    assert "descriptors = {d.id: d for d in self.service.provider_descriptors()}" in source
    assert "descriptor.credential_fields" in source
    assert "add_account_editor" in source


def test_technical_plugin_information_is_redirected_out_of_normal_account_flow():
    source = _settings_source()
    assert "Gérer les plugins et leurs capacités techniques…" in source
    assert "self.navigate('plugins')" in source
    assert "Les capacités internes des plugins restent dans Plugins et Diagnostic." in source
