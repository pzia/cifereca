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


def test_dashboard_header_actions_use_adaptive_bar():
    owner = MAIN_WINDOW.with_name("dashboard_page_controller.py")
    tree = ast.parse(owner.read_text(encoding="utf-8"))
    method = next(node for node in tree.body
                  if isinstance(node, ast.FunctionDef) and node.name == "build_dashboard_page")
    source = ast.unparse(method)
    facade = _method_source("MainWindow", "_build_dashboard_page")
    assert "dashboard_page_controller.build_dashboard_page" in facade

    assert "actions = AdaptiveActionBar(source=heading)" in source
    assert "Synchronisation rapide" in source
    assert "view-refresh-symbolic" in source
    assert "Vérifier toute la bibliothèque" in source
    assert "system-search-symbolic" in source
