from __future__ import annotations

import ast
from pathlib import Path


def test_core_kobo_vault_compatibility_module_is_absent():
    root = Path(__file__).resolve().parents[1]
    assert not (root / "src/cifereca/kobo/vault.py").exists()
    service = (root / "src/cifereca/services.py").read_text(encoding="utf-8")
    assert "from .kobo.vault import" not in service


def test_provider_owns_kobo_vault_client():
    source = Path(__file__).resolve().parents[1] / "src/plugins/provider-kobo/cifereca_kobo_vault.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    classes = {node.name for node in tree.body if isinstance(node, ast.ClassDef)}
    assert {"KoboVaultError", "KoboVaultClient"} <= classes
