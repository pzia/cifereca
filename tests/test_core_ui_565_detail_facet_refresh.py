from __future__ import annotations

import ast
from pathlib import Path

from cifereca.gui.query_model import QueryState, apply_facet


ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "src/cifereca/gui/main_window.py"
CONTROLS = ROOT / "src/cifereca/gui/query_controls.py"


def _method(path: Path, class_name: str, method_name: str) -> str:
    if path == MAIN and method_name == "_search_facet":
        owner = ast.parse(MAIN.with_name("facet_value_controller.py").read_text(encoding="utf-8"))
        return ast.unparse(next(node for node in owner.body
                                if isinstance(node, ast.FunctionDef) and node.name == "search_facet"))
    tree = ast.parse(path.read_text(encoding="utf-8"))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == method_name)
    return ast.unparse(method)


def test_language_facet_uses_same_multiselect_contract_as_top_bar():
    state = QueryState(text="roman", field="all", languages=("en",))
    updated = apply_facet(state, "language", "fr-FR")
    assert updated.text == "roman"
    assert updated.field == "all"
    assert updated.languages == ("fr",)


def test_detail_facet_updates_controls_silently_then_refreshes_once():
    source = _method(MAIN, "MainWindow", "_search_facet")
    assert "target_controls.set_state(updated, notify=False)" in source
    assert "window._render_library()" in source
    assert "window.run_search(False)" in source
    assert "self.library_controls.set_state(updated)" not in source
    assert "self.search_controls.set_state(updated)" not in source


def test_programmatic_state_can_skip_changed_callback_for_explicit_actions():
    source = _method(CONTROLS, "QueryControls", "set_state")
    assert "notify=True" in source
    assert "if notify:" in source
    assert "self._changed()" in source
    assert "self._last_notified_state = self.state()" in source


def test_same_facet_still_has_explicit_refresh_path():
    source = _method(MAIN, "MainWindow", "_search_facet")
    # No early return on updated == state: clicking an already-selected facet
    # still refreshes the current list/search as an explicit user action.
    assert "updated == state" not in source
    assert "target_controls.set_state(updated, notify=False)" in source
