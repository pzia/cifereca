from __future__ import annotations
from tests.gui_source import gui_source_text

import ast
from pathlib import Path


MAIN = Path(__file__).resolve().parents[1] / "src/cifereca/gui/main_window.py"
BOOK_WIDGETS = Path(__file__).resolve().parents[1] / "src/cifereca/gui/book_widgets.py"


def _tree():
    return ast.parse(gui_source_text(MAIN))


def _method(class_name: str, method_name: str) -> ast.FunctionDef:
    if class_name == "MainWindow" and method_name == "_add_book_notice":
        owner = ast.parse(MAIN.with_name("book_notice_controller.py").read_text(encoding="utf-8"))
        return next(n for n in owner.body
                    if isinstance(n, ast.FunctionDef) and n.name == "add_book_notice")
    if class_name == "MainWindow" and method_name == "_add_facets":
        owner = ast.parse(MAIN.with_name("detail_facets_controller.py").read_text(encoding="utf-8"))
        return next(n for n in owner.body if isinstance(n, ast.FunctionDef) and n.name == "add_facets")
    if class_name == "MainWindow" and method_name == "_library_selected":
        owner = ast.parse(MAIN.with_name("library_detail_controller.py").read_text(encoding="utf-8"))
        return next(n for n in owner.body if isinstance(n, ast.FunctionDef) and n.name == "library_selected")
    tree = _tree()
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
    return next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == method_name)


def _function(name: str) -> ast.FunctionDef:
    return next(node for node in _tree().body if isinstance(node, ast.FunctionDef) and node.name == name)


def _book_widget_method(class_name: str, method_name: str) -> ast.FunctionDef:
    tree = ast.parse(gui_source_text(BOOK_WIDGETS))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
    return next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == method_name)


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
    assert "detail_subtitle_lines(title, display_value)" in group


def test_search_library_and_loan_use_shared_essential_projection():
    expected = ["Source", "Format", "Langue", "Disponibilité"]
    for class_name, method_name in [
        ("DetailPanel", "show_search"),
        ("DetailPanel", "show_item_detail"),
        ("DetailPanel", "show_loan"),
        ("MainWindow", "_library_selected"),
    ]:
        source = ast.unparse(_method(class_name, method_name))
        if method_name == "_library_selected":
            assert "section_rows=local_book_detail_sections(" in source
            helper = ast.parse(MAIN.with_name(
                "local_book_detail_sections.py"
            ).read_text(encoding="utf-8"))
            source = ast.unparse(next(
                node for node in helper.body
                if isinstance(node, ast.FunctionDef)
                and node.name == "local_book_detail_sections"
            ))
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
    assert "'Achat Kobo · en ligne'" in state
    assert "'Achat Numilog · en ligne'" in state
    assert "'Notice de catalogue · sans droit attesté'" in state
    assert "remote_owned" in state


def test_notice_prioritizes_summary_and_filters_essential_duplicates():
    source = ast.unparse(_method("MainWindow", "_add_book_notice"))
    assert source.index("_show_description") < source.index("project_book_notice_groups")
    owner = ast.parse(MAIN.with_name("book_notice_controller.py").read_text(encoding="utf-8"))
    projection = ast.unparse(next(n for n in owner.body
                                  if isinstance(n, ast.FunctionDef)
                                  and n.name == "project_book_notice_groups"))
    constants = ast.unparse(owner)
    for label in ("Source", "Provenance", "Format", "Langue", "Langues", "Disponibilité"):
        assert repr(label) in constants
    assert "row[0] not in ESSENTIAL_NOTICE_LABELS" in projection
    assert projection.index("'Image du livre'") > projection.index("'Catalogue'")


def test_multivalue_facets_render_individual_filter_buttons_not_display_concatenations():
    source = ast.unparse(_method("MainWindow", "_add_facets"))
    assert "window._facet_value_group(choices, field, controls)" in source
    assert "len(choices) == 1" not in source
    assert "if not choices and field in subtitle_fallback_fields" in source
    assert "widget.set_subtitle('')" in source


def test_library_nominal_acquisition_state_and_single_presence_are_hidden():
    source = ast.unparse(_method("MainWindow", "_library_selected"))
    assert "section_rows=local_book_detail_sections(" in source
    helper = ast.parse(MAIN.with_name(
        "local_book_detail_sections.py"
    ).read_text(encoding="utf-8"))
    projection = ast.unparse(next(
        node for node in helper.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "local_book_detail_sections"
    ))
    assert "sync_label == 'À jour'" in projection
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


def test_unknown_file_format_promotes_support_to_essential_without_duplication():
    for method_name in ("show_search", "show_item_detail"):
        source = ast.unparse(_method("DetailPanel", method_name))
        essential = source.index("'Essentiel'")
        notice = source.index("'Notice'")
        support = source.index("'Support'", essential)
        assert essential < support < notice
        assert "essential_support = medium if not fmt else ''" in source
        assert "('Support', medium if fmt else '')" in source


def test_long_summaries_are_collapsible_but_remain_selectable():
    source = ast.unparse(_method("DetailPanel", "_show_description"))
    assert "set_selectable(True)" in source
    assert "len(text) > 700" not in source
    assert "text.count(" not in source
    assert "create_pango_layout(text)" in source
    assert "layout.get_line_count() > 6" in source
    assert "get_width()" in source
    assert "set_lines(6)" in source
    assert "Pango.EllipsizeMode.END" in source
    assert "'Lire la suite'" in source
    assert "'Réduire'" in source
    # Expanded/collapsed lines are now selected by the Pango overflow state.
    assert "lines = 6 if collapsed else -1" in source
    assert "label.set_lines(lines)" in source
    assert "Pango.EllipsizeMode.NONE" in source


def test_missing_detail_cover_compacts_only_after_absence_is_known():
    header = ast.unparse(_method("DetailPanel", "_cover_and_title"))
    bind_url = ast.unparse(_book_widget_method("CoverView", "bind_url"))
    apply_result = ast.unparse(_book_widget_method("CoverView", "_apply_result"))
    reset = ast.unparse(_book_widget_method("CoverView", "reset"))

    assert "CoverView(112, 168)" in header
    assert "set_missing_size(40, 60)" in header
    assert "bind_url(self.window.covers, thumbnail" in header
    assert "_set_logical_size(self._width, self._height)" in reset
    assert "_show_missing()" in bind_url
    assert "_show_missing()" in apply_result
    assert "_start_cover_request(force=True)" in apply_result


def test_long_metadata_is_fully_readable_without_tooltip():
    policy = ast.unparse(_function("detail_subtitle_lines"))
    group = ast.unparse(_method("DetailPanel", "_group"))

    assert "limit = 36 if section == 'Essentiel' else 72" in policy
    assert "if '\\n' in text" in policy
    assert "if len(text) > limit" in policy
    assert "return 0" in policy
    assert "return 1 if section == 'Essentiel' else 2" in policy
    assert "set_tooltip_text" in group
    assert "detail_subtitle_lines(title, display_value)" in group
