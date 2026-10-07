from __future__ import annotations

import ast
from pathlib import Path


MAIN = Path(__file__).resolve().parents[1] / "src/cifereca/gui/main_window.py"


def _tree():
    return ast.parse(MAIN.read_text(encoding="utf-8"))


def _method(class_name: str, method_name: str) -> ast.FunctionDef:
    tree = _tree()
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
    return next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == method_name)


def _function(name: str) -> ast.FunctionDef:
    return next(node for node in _tree().body if isinstance(node, ast.FunctionDef) and node.name == name)


def test_shared_projection_supports_ordered_sections_and_drops_filler_cells():
    projection = ast.unparse(_function("book_detail_presentation"))
    compact = ast.unparse(_function("compact_detail_rows"))
    assert "section_rows" in projection
    assert "dict.fromkeys" in projection
    assert "Inconnu" in compact
    assert "'—'" in compact


def test_book_header_is_compact_and_long_text_is_bounded():
    header = ast.unparse(_method("DetailPanel", "_cover_and_title"))
    assert "CoverView(112, 168)" in header
    assert "set_lines(3)" in header
    assert "set_lines(2)" in header
    assert "set_tooltip_text" in header


def test_essential_grid_is_two_columns_and_neutral_by_default():
    group = ast.unparse(_method("DetailPanel", "_group"))
    assert "'Essentiel'" in group
    assert "2 if title == 'Essentiel' else 3" in group
    assert "search_controls" not in group
    assert "set_subtitle_lines(1 if title == 'Essentiel'" in group


def test_search_library_and_loan_use_shared_essential_projection():
    expected = ["Source", "Format", "Langue", "Disponibilité"]
    for class_name, method_name in [
        ("DetailPanel", "show_search"),
        ("DetailPanel", "show_item_detail"),
        ("DetailPanel", "show_loan"),
        ("MainWindow", "_library_selected"),
    ]:
        source = ast.unparse(_method(class_name, method_name))
        assert "'Essentiel'" in source
        positions = [source.index(repr(label)) for label in expected]
        assert positions == sorted(positions), (class_name, method_name)


def test_availability_section_name_is_stable_during_async_refresh():
    initial = ast.unparse(_method("DetailPanel", "show_search"))
    loaded = ast.unparse(_method("DetailPanel", "show_item_detail"))
    assert "set_title('Disponibilités')" in initial
    assert "set_title('Disponibilités')" in loaded
    assert "Exemplaires (" not in loaded
    assert "Détail des exemplaires non fourni par cette source" in loaded


def test_remote_owned_is_not_mislabeled_as_generic_available():
    state = ast.unparse(_method("MainWindow", "_library_state_label"))
    assert "'Possédé en ligne'" in state
    assert "remote_owned" in state


def test_notice_prioritizes_summary_and_filters_essential_duplicates():
    source = ast.unparse(_method("MainWindow", "_add_book_notice"))
    assert source.index("_show_description") < source.index("notice_rows")
    for label in ("Source", "Provenance", "Format", "Langue", "Langues", "Disponibilité"):
        assert repr(label) in source
    assert source.index("'Image du livre'") > source.index("'Catalogue'")


def test_multivalue_facets_are_not_bound_to_display_concatenations():
    source = ast.unparse(_method("MainWindow", "_add_facets"))
    assert "len(choices) == 1" in source
    assert "widget.get_subtitle()" not in source[source.index("if choices and len(choices) == 1"):]


def test_library_nominal_acquisition_state_and_single_presence_are_hidden():
    source = ast.unparse(_method("MainWindow", "_library_selected"))
    assert "sync_label == 'À jour'" in source
    assert "len(unique_presence) > 1" in source


def test_async_detail_refresh_preserves_logical_action_focus():
    presentation = ast.unparse(_method("DetailPanel", "show_presentation"))
    actions = ast.unparse(_method("DetailPanel", "_actions"))
    focus_key = ast.unparse(_method("DetailPanel", "_focused_action_key"))
    identity = ast.unparse(_method("DetailPanel", "_action_focus_key"))

    assert "_pending_action_focus_key = self._focused_action_key()" in presentation
    assert presentation.index("_focused_action_key") < presentation.index("_clear_actions")
    assert "button.grab_focus()" in actions
    assert "_pending_action_focus_key = ''" in actions
    assert "_cifereca_action_id" in identity
    assert "_cifereca_action_label" in identity
    assert "has_focus()" in focus_key
