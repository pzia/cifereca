from __future__ import annotations
from tests.gui_source import gui_source_text

import ast
from pathlib import Path
from types import SimpleNamespace as NS

from cifereca.gui.action_layout import action_budget, compact_actions


SOURCE = Path(__file__).resolve().parents[1] / "src/cifereca/gui/main_window.py"


def _method(class_name: str, method_name: str, namespace=None):
    if class_name == "MainWindow" and method_name == "_add_facets":
        from cifereca.gui.detail_facets_controller import add_facets
        dependencies = dict(namespace or {})
        return lambda host, detail, item, controls: add_facets(
            host, detail, item, controls,
            Adw=dependencies["Adw"],
            Gtk=dependencies.get("Gtk", NS()),
            TaskPriority=dependencies["TaskPriority"],
        )
    tree = ast.parse(gui_source_text(SOURCE))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == method_name)
    ns = dict(namespace or {})
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(SOURCE), "exec"), ns)
    return ns[method_name]


def test_repeated_action_decoration_deduplicates_open_and_send_actions():
    host = NS(action_scroll=NS(append=lambda item: host.appended.append(item), set_visible=lambda value: None), _action_ids=set())
    host.appended = []
    fn = _method("DetailPanel", "_actions")

    opening = NS(_cifereca_action_id="open-local")
    send = NS(_cifereca_action_id="send-local:other")
    fn(host, [opening, send])
    fn(host, [NS(_cifereca_action_id="open-local"), NS(_cifereca_action_id="send-local:other")])

    assert host.appended == [opening, send]


def test_library_group_uses_same_three_column_truncation_contract_as_notice():
    tree = ast.parse(gui_source_text(SOURCE))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "DetailPanel")
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == "_group")
    source = ast.unparse(method)

    assert "'Bibliothèque'" in source
    assert "columns = 2 if title == 'Essentiel' else 3" in source
    assert "set_max_children_per_line(columns)" in source
    assert "detail_display_value(label, value)" in source
    assert "detail_subtitle_lines(title, display_value)" in source
    assert "set_tooltip_text(label + ' : ' + tooltip_value)" in source


def test_action_width_budget_has_exact_fit_boundary():
    assert action_budget(1000) == 500
    assert compact_actions([247, 247], 500) is False
    assert compact_actions([248, 248], 500) is True
    assert action_budget(-1) == 0


def test_detail_facets_replace_previous_inline_value_group_without_footer_tags():
    owner = ast.parse(SOURCE.with_name("detail_facets_controller.py").read_text(encoding="utf-8"))
    method = next(node for node in owner.body
                  if isinstance(node, ast.FunctionDef) and node.name == "add_facets")
    source = ast.unparse(method)

    assert "_append_content" not in source
    assert "_cifereca_facet_values" in source
    assert "widget.remove(previous)" in source
    assert "widget.add_suffix(facet_values)" in source


class _FacetGroup:
    def __init__(self, choices, field, controls):
        self.choices = list(choices)
        self.field = field
        self.controls = controls
        self.visible = True
        self.tooltip = ""

    def set_tooltip_text(self, text):
        self.tooltip = text

    def set_visible(self, value):
        self.visible = value


class _Row:
    def __init__(self, title="Provenance", subtitle="Bibliothèques de Paris"):
        self.title = title
        self.subtitle = subtitle
        self.active = False
        self.suffixes = []
        self.removed = []

    def get_title(self):
        return self.title

    def get_subtitle(self):
        return self.subtitle

    def set_subtitle(self, value):
        self.subtitle = value

    def set_activatable(self, value):
        self.active = value

    def add_suffix(self, widget):
        self.suffixes.append(widget)

    def remove(self, widget):
        self.removed.append(widget)
        if widget in self.suffixes:
            self.suffixes.remove(widget)

    def get_first_child(self):
        return None


def _facet_host(values):
    return NS(
        _facet_values=lambda item: list(values),
        _facet_value_group=lambda choices, field, controls: _FacetGroup(choices, field, controls),
        service=NS(current_identity=NS(identity_id="id"), adept_return_for_artifact=lambda *args: None),
    )


def test_inline_source_uses_provider_id_and_rebinding_replaces_value_group():
    row = _Row()
    previous = _FacetGroup([("Ancienne", "old")], "source", object())
    row._cifereca_facet_values = previous
    row.suffixes.append(previous)
    host = _facet_host([("source", "Bibliothèques de Paris", "paris")])
    detail = NS(content=row, _subtitle_label=None, add_actions=lambda buttons: None)
    controls = object()
    fn = _method(
        "MainWindow",
        "_add_facets",
        {"Adw": NS(ActionRow=_Row), "TaskPriority": NS(USER_ACTION=0)},
    )

    fn(host, detail, {}, controls)

    assert previous in row.removed
    assert row.active is False
    assert row.subtitle == ""
    current = row._cifereca_facet_values
    assert current.field == "source"
    assert current.choices == [("Bibliothèques de Paris", "paris")]
    assert current.controls is controls


def test_epub_only_publisher_uses_displayed_subtitle_as_filter_value():
    row = _Row(title="Éditeur", subtitle="Éditeur EPUB")
    host = _facet_host([])
    detail = NS(content=row, _subtitle_label=None, add_actions=lambda buttons: None)
    controls = object()
    fn = _method(
        "MainWindow",
        "_add_facets",
        {"Adw": NS(ActionRow=_Row), "TaskPriority": NS(USER_ACTION=0)},
    )

    fn(host, detail, {}, controls)

    current = row._cifereca_facet_values
    assert current.field == "publisher"
    assert current.choices == [("Éditeur EPUB", "Éditeur EPUB")]
    assert row.subtitle == ""
