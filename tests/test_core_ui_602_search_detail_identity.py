from __future__ import annotations

from tests.gui_source import gui_source_text

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "src/cifereca/gui/main_window.py"


def _method(name: str) -> str:
    if name == "_activate_identity":
        owner = ast.parse(MAIN.with_name("identity_activation_controller.py").read_text(encoding="utf-8"))
        return ast.unparse(next(node for node in owner.body
                                if isinstance(node, ast.FunctionDef) and node.name == "activate_identity"))
    tree = ast.parse(gui_source_text(MAIN))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "MainWindow")
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == name)
    return ast.unparse(method)


def test_identity_change_keeps_explicit_transitional_detail_state():
    source = _method("_activate_identity")
    assert "window.search_page.detail.empty('Nouvelle identité', 'Lancez une recherche dans le connu.')" in source
    assert "window._search_generation" in source
    assert "window._selected_search_id = None" in source


def test_successful_local_search_reconciles_stale_detail_state():
    source = _method("_populate_local_search")
    assert "shown = self._render_search_results()" in source
    assert "self._sync_search_detail_after_results()" in source
    assert source.index("self._render_search_results()") < source.index("self._sync_search_detail_after_results()")


def test_successful_remote_search_reconciles_stale_detail_state():
    source = _method("_populate_search")
    assert "self._sync_search_detail_after_results()" in source


def test_detail_reconciliation_preserves_real_selection_and_neutralizes_none():
    source = _method("_sync_search_detail_after_results")
    assert "self.search_page.selection.get_selected_item()" in source
    assert "isinstance(selected, BookListItem)" in source
    assert "self._search_selected(None)" in source


def test_search_callbacks_remain_identity_generation_guarded():
    source = _method("run_search")
    assert "generation == self._search_generation" in source
    assert "identity_id == identity" in source
    assert "if current():" in source
