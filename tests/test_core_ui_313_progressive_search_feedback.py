from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN_WINDOW = ROOT / "src/cifereca/gui/main_window.py"


def _method_source(name: str) -> str:
    tree = ast.parse(MAIN_WINDOW.read_text())
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "MainWindow")
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == name)
    return ast.unparse(method)


def test_progressive_batches_update_results_without_toast():
    source = _method_source("run_search")
    assert "self._populate_search(self._remote_search_results, announce=False)" in source
    assert "self._populate_search(self._remote_search_results, announce=True)" in source
    assert "recherche indisponible" not in source


def test_search_result_toast_is_reserved_for_announced_render():
    source = _method_source("_populate_search")
    assert "announce: bool=True" in source.replace(" ", "")
    assert "if not announce:" in source
    assert "self.toast" in source
