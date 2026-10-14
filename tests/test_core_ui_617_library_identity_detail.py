from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "src/cifereca/gui/main_window.py"


def _method(name: str) -> str:
    tree = ast.parse(MAIN.read_text(encoding="utf-8"))
    if name == "_library_selected":
        tree = ast.parse(MAIN.with_name("library_detail_controller.py").read_text(encoding="utf-8"))
        return ast.unparse(next(node for node in tree.body
                                if isinstance(node, ast.FunctionDef) and node.name == "library_selected"))
    if name in {"_render_library", "_render_recent"}:
        owner = MAIN.with_name("library_render_controller.py")
        module = ast.parse(owner.read_text(encoding="utf-8"))
        real_name = name.removeprefix("_")
        return ast.unparse(next(node for node in module.body
                                if isinstance(node, ast.FunctionDef)
                                and node.name == real_name))
    if name == "refresh_library":
        tree = ast.parse(MAIN.with_name("library_refresh.py").read_text(encoding="utf-8"))
        return ast.unparse(next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "MainWindow")
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == name)
    return ast.unparse(method)


def test_library_none_selection_has_neutral_detail_state():
    source = _method("_library_selected")
    assert "if not row:" in source
    assert "window.library_page.detail.empty(" in source
    assert "Sélectionnez un livre" in source


def test_rendered_library_resets_stale_detail_when_no_selection_restored():
    source = _method("_render_library")
    assert "restored_pending = window._select_pending_library_item()" in source
    assert "window.library_page.selection.get_selected_item() is None" in source
    assert "window._library_selected(None)" in source


def test_identity_change_clears_old_library_before_async_refresh():
    source = _method("_activate_identity")
    assert "self._render_library([])" in source
    assert "self.library_page.detail.empty" in source
    assert "Nouvelle identité" in source
    assert "Chargement de la bibliothèque…" in source


def test_library_refresh_discards_obsolete_identity_callback():
    source = _method("refresh_library")
    assert "generation = self._identity_context_generation" in source
    assert "not self._identity_request_current(identity_id, generation)" in source
    assert "self.refresh_library(False)" in source
    assert "self._show_library_entries(entries)" in source
