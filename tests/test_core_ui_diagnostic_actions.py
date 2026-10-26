from __future__ import annotations
from tests.settings_source import settings_source_text
from tests.gui_source import gui_source_text

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN_WINDOW = ROOT / "src/cifereca/gui/main_window.py"

def _method(class_name: str, method_name: str) -> ast.FunctionDef:
    tree = ast.parse(gui_source_text(MAIN_WINDOW))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
    return next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == method_name)

def test_settings_diagnostic_actions_use_adaptive_bar_and_action_buttons():
    # MainWindow owns state/callbacks; the extracted builder owns GTK layout.
    method = _method("MainWindow", "_build_settings_page")
    host = ast.unparse(ast.parse(settings_source_text()))
    assert "build_settings_diagnostics_section(" in host
    assert "adaptive_action_bar=AdaptiveActionBar" in host
    assert "action_button=self.action_button" in host

    extracted = ROOT / "src/cifereca/gui/settings_diagnostics_section.py"
    tree = ast.parse(gui_source_text(extracted))
    builder = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "build_settings_diagnostics_section"
    )
    source = ast.unparse(builder)
    assert "buttons = adaptive_action_bar(source=section)" in source
    assert "section.append(buttons)" in source

    calls = []
    for node in ast.walk(builder):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.func.id != "action_button":
            continue
        args = [arg.value for arg in node.args if isinstance(arg, ast.Constant)]
        keywords = {
            kw.arg: kw.value.value for kw in node.keywords
            if kw.arg and isinstance(kw.value, ast.Constant)
        }
        calls.append((args, keywords))

    assert (["Actualiser", "view-refresh-symbolic"], {}) in calls
    assert any(args[1:] == ["edit-copy-symbolic"] and keywords.get("suggested") is True for args, keywords in calls)
    assert (["Exporter l’affichage", "document-save-symbolic"], {}) in calls
    assert any(args[1:] == ["edit-clear-symbolic"] for args, _keywords in calls)
    assert all("connect(" not in ast.unparse(node) for node in builder.body)
