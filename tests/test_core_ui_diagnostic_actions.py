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

def test_settings_diagnostic_actions_use_adaptive_bar_and_action_buttons():
    source = _method_source("MainWindow", "_build_settings_page")
    assert "buttons = AdaptiveActionBar(source=box)" in source
    assert 'self.action_button("Actualiser", "view-refresh-symbolic")' in source
    assert 'self.action_button("Copier dans le presse-papiers", "edit-copy-symbolic", suggested=True)' in source
    assert 'self.action_button("Effacer", "edit-clear-symbolic")' in source
