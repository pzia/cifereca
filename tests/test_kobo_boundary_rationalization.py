from __future__ import annotations

import ast
from pathlib import Path


def test_core_kobo_vault_has_no_http_client_implementation():
    source = Path(__file__).resolve().parents[1] / "src/cifereca/kobo/vault.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    classes = {node.name for node in tree.body if isinstance(node, ast.ClassDef)}
    assert classes == {"KoboVaultError"}

    imports = {
        alias.name
        for node in tree.body
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert "httpx" not in imports


def test_provider_owns_kobo_vault_client():
    source = Path(__file__).resolve().parents[1] / "plugins/provider-kobo/cifereca_kobo_vault.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    classes = {node.name for node in tree.body if isinstance(node, ast.ClassDef)}
    assert {"KoboVaultError", "KoboVaultClient"} <= classes
