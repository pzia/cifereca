from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN_WINDOW = ROOT / "src/cifereca/gui/main_window.py"

def _method(class_name: str, method_name: str) -> ast.FunctionDef:
    tree = ast.parse(MAIN_WINDOW.read_text())
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
    return next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == method_name)

def test_settings_diagnostic_actions_use_adaptive_bar_and_action_buttons():
    method = _method("MainWindow", "_build_settings_page")
    source = ast.unparse(method)
    assert "buttons = AdaptiveActionBar(source=box)" in source

    calls = []
    for node in ast.walk(method):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if not isinstance(node.func.value, ast.Name) or node.func.value.id != "self" or node.func.attr != "action_button":
            continue
        args = [arg.value for arg in node.args if isinstance(arg, ast.Constant)]
        keywords = {kw.arg: kw.value.value for kw in node.keywords if kw.arg and isinstance(kw.value, ast.Constant)}
        calls.append((args, keywords))

    assert (["Actualiser", "view-refresh-symbolic"], {}) in calls
    assert any(args[1:] == ["edit-copy-symbolic"] and keywords.get("suggested") is True for args, keywords in calls)
    assert (["Exporter le diagnostic", "document-save-symbolic"], {}) in calls
    assert any(args[1:] == ["edit-clear-symbolic"] for args, _keywords in calls)
