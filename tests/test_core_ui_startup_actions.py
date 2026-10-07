from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN_WINDOW = ROOT / "src/cifereca/gui/main_window.py"


def _method_source(class_name: str, method_name: str) -> str:
    tree = ast.parse(MAIN_WINDOW.read_text())
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == method_name)
    return ast.unparse(method)


def test_startup_actions_use_adaptive_bar():
    source = _method_source("MainWindow", "_build_startup")

    assert "actions = AdaptiveActionBar(source=card)" in source
    assert "Connecter les sources" in source
    assert "Configurer les sources" in source
    assert "connect_configured_sources" in source
    assert "_open_prelogin_diagnostics" in source
