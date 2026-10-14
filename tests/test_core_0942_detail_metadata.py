"""Focused, no-GTK regression checks for the shared metadata detail wiring."""
from __future__ import annotations
from tests.gui_source import gui_source_text

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "src/cifereca/gui/main_window.py"


def _method(class_name, method_name):
    if class_name == "MainWindow" and method_name == "_library_selected":
        owner = ast.parse(MAIN.with_name("library_detail_controller.py").read_text(encoding="utf-8"))
        return next(n for n in owner.body if isinstance(n, ast.FunctionDef)
                    and n.name == "library_selected")
    tree = ast.parse(gui_source_text(MAIN))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
    return next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == method_name)


def _projection_calls(node):
    return [
        call for call in ast.walk(node)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
        and call.func.id == "book_detail_presentation"
    ]


def test_search_and_library_pass_metadata_into_shared_projection():
    for class_name, method_name in (
        ("DetailPanel", "show_search"),
        ("DetailPanel", "show_item_detail"),
        ("MainWindow", "_search_selected"),
        ("MainWindow", "_library_selected"),
    ):
        calls = _projection_calls(_method(class_name, method_name))
        assert calls, method_name
        assert any(any(kw.arg == "metadata" for kw in call.keywords) for call in calls), method_name


def test_detail_section_uses_collapsible_rows_without_network():
    source = ast.unparse(_method("DetailPanel", "_group"))
    assert "title == 'Détail'" in source
    assert "Adw.ExpanderRow()" in source
    assert "expander.add_row(row)" in source
    assert "notify::expanded" in source
    assert "set_subtitle_lines(0)" in source
    assert "self._detail_expanded" in source
    assert "http" not in source.lower()


def test_shared_builder_keeps_essential_and_detail_sections_separate():
    tree = ast.parse(gui_source_text(MAIN))
    builder = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "book_detail_presentation")
    assert any(arg.arg == "metadata" for arg in builder.args.kwonlyargs)
    source = ast.unparse(builder)
    assert "detail_metadata_items(metadata)" in source
    assert "title='Détail'" in source or "title='Détail'".replace("'", '"') in source
    assert "already_shown" in source
    assert "primary_title" in source


def test_shared_facets_do_not_replace_multivalued_detail_rows():
    """Actual method with fake GTK widgets: Bob/nature stay individual."""
    from types import SimpleNamespace as NS

    class Node:
        def __init__(self, children=()):
            self.children = list(children)
            self._siblings()
            self.tooltip = None

        def _siblings(self):
            for i, child in enumerate(self.children):
                child._next = self.children[i + 1] if i + 1 < len(self.children) else None

        def get_first_child(self):
            return self.children[0] if self.children else None

        def get_next_sibling(self):
            return getattr(self, "_next", None)

        def set_tooltip_text(self, text):
            self.tooltip = text

        def set_visible(self, visible):
            self.visible = visible

        def add_css_class(self, _class):
            pass

    class Row(Node):
        def __init__(self, title, subtitle, children=()):
            super().__init__(children)
            self.title = title
            self.subtitle = subtitle
            self.activatable = None
            self.suffixes = []

        def get_title(self):
            return self.title

        def get_subtitle(self):
            return self.subtitle

        def set_subtitle(self, value):
            self.subtitle = value

        def set_activatable(self, value):
            self.activatable = value

        def add_suffix(self, widget):
            self.suffixes.append(widget)
            self.children.append(widget)
            self._siblings()

    class Expander(Row):
        pass

    method = _method("MainWindow", "_add_facets")
    module = ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[]))
    namespace = {"Adw": NS(ActionRow=Row, ExpanderRow=Expander)}
    exec(compile(module, str(MAIN), "exec"), namespace)

    alice = Row("Auteur", "Alice Exemple")
    bob = Row("Auteur", "Bob Exemple")
    nature = Row("Sujet", "nature")
    maison_b = Row("Éditeur", "Maison B")
    # No Python marker on the Expander: the guard must use native GTK identity.
    bibliographic = Expander("Détail", "4 champs", (alice, bob, nature, maison_b))
    summary = Row("Auteur", "Alice Exemple")
    detail = NS(content=Node((bibliographic, summary)), _subtitle_label=None)
    calls = []

    class Main:
        service = NS(current_identity=None,
                     adept_return_for_artifact=lambda *_: None)

        def _facet_values(self, _item):
            return [
                ("author", "Alice Exemple", "Alice Exemple"),
                ("author", "Bob Exemple", "Bob Exemple"),
                ("subject", "nature", "nature"),
                ("publisher", "Maison B", "Maison B"),
            ]

        def _facet_value_group(self, values, field, _controls):
            calls.append((field, values))
            return Node()

    main = Main()
    for _ in range(2):
        namespace["_add_facets"](main, detail, {}, object())
    assert [r.get_subtitle() for r in (alice, bob, nature, maison_b)] == [
        "Alice Exemple", "Bob Exemple", "nature", "Maison B",
    ]
    assert all(r.activatable is None and not r.suffixes
               for r in (alice, bob, nature, maison_b))
    assert bibliographic.activatable is None
    assert summary.get_subtitle() == ""
    assert summary.suffixes
    assert any(field == "author" for field, _ in calls)


def test_collapsible_detail_is_marked_before_facet_traversal():
    group = ast.unparse(_method("DetailPanel", "_group"))
    facet = ast.unparse(_method("MainWindow", "_add_facets"))
    assert "Adw.ExpanderRow()" in group
    assert 'isinstance(widget, getattr(Adw, \'ExpanderRow\', ()))' in facet
    assert "widget.get_title() == 'Détail'" in facet
    assert "_cifereca_preserve_bibliographic_rows" not in facet


def test_detail_copy_uses_gtk4_content_provider_with_explicit_feedback():
    """GTK4 bindings on Debian can lack GdkX11Clipboard.set_text."""
    source = ast.unparse(_method("DetailPanel", "_group"))
    assert "Gdk.ContentProvider.new_for_value(value)" in source
    assert "self.window._clipboard_provider = provider" in source
    assert "display.get_clipboard().set_content(provider)" in source
    assert 'self.window.toast(\'Copié\')' in source
    assert 'self.window.toast(\'Copie impossible\')' in source
    assert "get_clipboard().set_text(" not in source
