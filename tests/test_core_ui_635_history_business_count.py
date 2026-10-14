from __future__ import annotations
from tests.gui_source import gui_source_text

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "src/cifereca/gui/main_window.py"


def _method(class_name: str, method_name: str) -> str:
    tree = ast.parse(gui_source_text(MAIN))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == method_name)
    return ast.unparse(method)


def test_history_uses_business_total_count_mode():
    owner = ast.parse(MAIN.with_name("secondary_page_controller.py").read_text(encoding="utf-8"))
    source = ast.unparse(next(n for n in owner.body
                              if isinstance(n, ast.FunctionDef) and n.name == "build_history_page"))
    assert "page.set_total_only_count(True)" in source
    facade = _method("MainWindow", "_build_history_page")
    assert "secondary_page_controller.build_history_page" in facade


def test_total_only_mode_never_exposes_materialized_batch_size():
    source = _method("MasterDetailPage", "_update_count")
    assert "hasattr(self, '_count_total_only')" in source
    assert "self._count_total_only" in source
    assert "self._item_count_label(self._total_count)" in source


def test_default_master_detail_keeps_loaded_over_total_behavior_for_other_pages():
    source = _method("MasterDetailPage", "_update_count")
    assert "self._total_count > displayed" in source
    assert "displayed" in source
    assert "self._count_total_only" in source


def test_history_progressive_append_keeps_same_business_total():
    owner = ast.parse(MAIN.with_name("secondary_results_controller.py").read_text(encoding="utf-8"))
    source = {method.name: ast.unparse(method) for method in owner.body
              if isinstance(method, ast.FunctionDef)}
    populate = source["populate_history"]
    append = source["append_history_chunk"]
    assert "total_count=len(window._history_rows)" in populate
    assert "total_count=len(window._history_rows)" in append
    assert "secondary_results_controller.populate_history" in _method("MainWindow", "_populate_history")
    assert "secondary_results_controller.append_history_chunk" in _method("MainWindow", "_append_history_chunk")


def test_total_count_pluralization_is_user_facing():
    source = _method("MasterDetailPage", "_item_count_label")
    assert "élément" in source
    assert "éléments" in source
