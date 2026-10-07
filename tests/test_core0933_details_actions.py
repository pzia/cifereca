from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS

from cifereca.gui.action_layout import action_budget, compact_actions


SOURCE = Path(__file__).resolve().parents[1] / "src/cifereca/gui/main_window.py"


def _method(class_name: str, method_name: str, namespace=None):
    tree = ast.parse(SOURCE.read_text())
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
    tree = ast.parse(SOURCE.read_text())
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


def test_detail_facets_do_not_append_duplicate_footer_tags_and_disconnect_previous_handlers():
    tree = ast.parse(SOURCE.read_text())
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "MainWindow")
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == "_add_facets")
    calls = [node for node in ast.walk(method) if isinstance(node, ast.Call)]

    assert not any(isinstance(node.func, ast.Attribute) and node.func.attr == "_append_content" for node in calls)
    assert any(isinstance(node.func, ast.Attribute) and node.func.attr == "disconnect" for node in calls)


class _Image:
    @classmethod
    def new_from_icon_name(cls, _name):
        return cls()

    def set_tooltip_text(self, _text):
        pass


class _Row:
    def __init__(self, title="Provenance", subtitle="Bibliothèques de Paris"):
        self.title = title
        self.subtitle = subtitle
        self.handlers = {}
        self.serial = 0

    def get_title(self):
        return self.title

    def get_subtitle(self):
        return self.subtitle

    def set_activatable(self, value):
        self.active = value

    def connect(self, event, callback):
        self.serial += 1
        self.handlers[self.serial] = callback
        return self.serial

    def disconnect(self, identifier):
        del self.handlers[identifier]

    def get_first_child(self):
        return None


def test_inline_source_uses_provider_id_and_rebinding_does_not_duplicate_handler():
    row = _Row()
    row._cifereca_facet_handler = row.connect("activated", lambda *_: None)
    calls = []
    host = NS(
        _facet_values=lambda item: [("source", "Bibliothèques de Paris", "paris")],
        _search_facet=lambda field, value, controls: calls.append((field, value, controls)),
        service=NS(current_identity=NS(identity_id="id"), adept_return_for_artifact=lambda *args: None),
    )
    detail = NS(content=row, _subtitle_label=None, add_actions=lambda buttons: None)
    controls = object()
    adw = NS(ActionRow=_Row)
    fn = _method("MainWindow", "_add_facets", {"Adw": adw, "Gtk": NS(Button=object, Image=_Image), "TaskPriority": NS(USER_ACTION=0)})

    fn(host, detail, {}, controls)
    fn(host, detail, {}, controls)

    assert len(row.handlers) == 1
    next(iter(row.handlers.values()))(row)
    assert calls == [("source", "paris", controls)]


def test_epub_only_publisher_remains_searchable_from_inline_detail():
    row = _Row(title="Éditeur", subtitle="Éditeur EPUB")
    calls = []
    host = NS(
        _facet_values=lambda item: [],
        _search_facet=lambda field, value, controls: calls.append((field, value, controls)),
        service=NS(current_identity=NS(identity_id="id"), adept_return_for_artifact=lambda *args: None),
    )
    detail = NS(content=row, _subtitle_label=None, add_actions=lambda buttons: None)
    controls = object()
    fn = _method("MainWindow", "_add_facets", {"Adw": NS(ActionRow=_Row), "Gtk": NS(Button=object, Image=_Image), "TaskPriority": NS(USER_ACTION=0)})

    fn(host, detail, {}, controls)

    next(iter(row.handlers.values()))(row)
    assert calls == [("publisher", "Éditeur EPUB", controls)]
