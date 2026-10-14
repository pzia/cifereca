from __future__ import annotations
from tests.gui_source import gui_source_text

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "src/cifereca/gui/main_window.py"


def _method(name: str) -> str:
    if name in {"_search_facet", "_facet_value_group", "_facet_value_button"}:
        target = name.lstrip("_")
        owner = ast.parse(MAIN.with_name("facet_value_controller.py").read_text(encoding="utf-8"))
        return ast.unparse(next(n for n in owner.body
                                if isinstance(n, ast.FunctionDef) and n.name == target))
    if name == "_add_facets":
        owner = ast.parse(MAIN.with_name("detail_facets_controller.py").read_text(encoding="utf-8"))
        return ast.unparse(next(n for n in owner.body if isinstance(n, ast.FunctionDef) and n.name == "add_facets"))
    tree = ast.parse(gui_source_text(MAIN))
    cls = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "MainWindow"
    )
    method = next(
        node for node in cls.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )
    return ast.unparse(method)


def test_filterable_value_itself_is_the_native_button_target():
    source = _method("_facet_value_button")
    assert "button = Gtk.Button()" in source
    assert "button.add_css_class('flat')" in source
    assert "button.add_css_class('facet-value')" in source
    assert "Filtrer par" in source
    assert "Gtk.AccessibleProperty.LABEL" in source
    assert "button.connect('clicked'" in source
    assert "window._search_facet" in source


def test_filterable_values_wrap_consistently():
    source = _method("_facet_value_group")
    assert "group = Gtk.FlowBox()" in source
    assert "group.set_max_children_per_line(4)" in source
    assert "window._facet_value_button" in source


def test_all_supported_detail_facets_share_same_affordance():
    source = _method("_add_facets")
    for field in (
        "source", "format", "support", "author", "publisher",
        "subject", "collection", "series", "isbn", "language",
    ):
        assert repr(field) in source
    assert "window._facet_value_group(choices, field, controls)" in source
    assert "widget.set_activatable(False)" in source


def test_detail_facets_no_longer_use_distant_search_icon_or_blue_author_links():
    source = _method("_add_facets")
    assert "system-search-symbolic" not in source
    assert "activate-link" not in source
    assert "set_markup" not in source
    assert "<a href" not in source


def test_author_subtitle_uses_same_filter_value_buttons():
    source = _method("_add_facets")
    assert "subtitle.set_visible(False)" in source
    assert "window._facet_value_group(authors, 'author', controls)" in source
    assert "parent.append(author_box)" in source


def test_facet_css_has_explicit_hover_and_focus_states():
    source = _method("_install_css")
    assert ".facet-value" in source
    assert ".facet-value:hover" in source
    assert ".facet-value:focus" in source
