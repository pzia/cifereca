from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN_WINDOW = ROOT / "src/cifereca/gui/sync_controller.py"


def _method_source(class_name: str, method_name: str) -> str:
    tree = ast.parse(MAIN_WINDOW.read_text())
    method = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == method_name)
    return ast.unparse(method)


def test_sync_header_actions_keep_refresh_and_scoped_sync_commands():
    source = _method_source("MainWindow", "_build_sync_page")

    assert "Actualiser" in source
    assert "view-refresh-symbolic" in source
    assert "Synchroniser les sources" in source
    assert "self._sync_all_button = all_now" in source
    assert "emblem-synchronizing-symbolic" in source
    assert "add_sync_action(heading, refresh)" in source
    assert "add_sync_action(heading, all_now)" in source
