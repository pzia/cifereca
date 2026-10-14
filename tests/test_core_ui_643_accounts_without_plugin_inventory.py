from __future__ import annotations
from tests.settings_source import settings_source_text

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "src/cifereca/gui/main_window.py"
ACCOUNTS = ROOT / "src/cifereca/gui/settings_accounts_section.py"


def _method(path: Path, name: str, class_name: str | None = None) -> str:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    nodes = tree.body
    if class_name:
        cls = next(node for node in nodes if isinstance(node, ast.ClassDef) and node.name == class_name)
        nodes = cls.body
    return ast.unparse(next(node for node in nodes if isinstance(node, ast.FunctionDef) and node.name == name))


def test_accounts_tab_starts_with_identity_configuration_not_plugin_inventory():
    identity = _method(ACCOUNTS, "build_identity_settings_header")
    provider = _method(ACCOUNTS, "build_provider_accounts_header")
    main = ast.unparse(ast.parse(settings_source_text()))
    source = identity + provider + main
    assert "Plugins fournisseurs" not in source
    assert "descriptor.capabilities.names()" not in source
    assert "Plugin fournisseur" not in source
    assert "identities_title = Gtk.Label(label='Identités'" in identity
    assert "accounts_title = Gtk.Label(label='Sources et comptes fournisseurs'" in provider
    assert main.index("build_identity_settings_header(") < main.index("build_provider_accounts_header(")


def test_provider_descriptors_still_drive_account_editors_without_exposing_capability_codes():
    source = ast.unparse(ast.parse(settings_source_text()))
    buttons = _method(ACCOUNTS, "build_provider_account_buttons")
    assert "descriptors = {d.id: d for d in self.service.provider_descriptors()}" in source
    assert "descriptor.credential_fields" in buttons
    assert "build_provider_account_editor" in source
    assert "add_account_editor" in source


def test_technical_plugin_information_is_redirected_out_of_normal_account_flow():
    headers = _method(ACCOUNTS, "build_provider_accounts_header")
    source = ast.unparse(ast.parse(settings_source_text()))
    assert "Gérer les plugins et leurs capacités techniques…" in headers
    assert "self.navigate('plugins')" in source
    assert "Les capacités internes des plugins restent dans Plugins et Diagnostic." in headers
