from __future__ import annotations
from tests.gui_source import gui_source_text

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN_WINDOW = ROOT / "src/cifereca/gui/main_window.py"
ACTION_WIDGETS = ROOT / "src/cifereca/gui/action_widgets.py"


def _class_method_source(path: Path, class_name: str, method_name: str) -> str:
    tree = ast.parse(gui_source_text(path))
    cls = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == class_name
    )
    method = next(
        node for node in cls.body
        if isinstance(node, ast.FunctionDef) and node.name == method_name
    )
    return ast.unparse(method)


def test_detail_panel_keeps_actions_above_scrollable_body():
    source = _class_method_source(MAIN_WINDOW, "DetailPanel", "__init__")

    action_create = "self.action_scroll = AdaptiveActionBar("
    action_append = "self.append(self.action_scroll)"
    body_create = "self.body_scroll = Gtk.ScrolledWindow()"
    body_append = "self.append(self.body_scroll)"
    content_attach = "self.body_scroll.set_child(self.content)"

    for statement in (action_create, action_append, body_create, body_append, content_attach):
        assert statement in source

    assert source.index(action_create) < source.index(action_append)
    assert source.index(action_append) < source.index(body_create)
    assert source.index(body_create) < source.index(body_append)
    assert source.index(body_append) < source.index(content_attach)


def test_detail_body_is_vertically_scrollable_and_parent_bounded():
    source = _class_method_source(MAIN_WINDOW, "DetailPanel", "__init__")

    assert "self.set_orientation(Gtk.Orientation.VERTICAL)" in source
    assert "self.set_vexpand(True)" in source
    assert "self.body_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)" in source
    assert "self.body_scroll.set_propagate_natural_height(False)" in source
    assert "self.body_scroll.set_min_content_height(0)" in source
    assert "self.body_scroll.set_vexpand(True)" in source
    assert "self.body_scroll.set_hexpand(True)" in source


def test_detail_actions_use_one_line_horizontal_fallback():
    source = _class_method_source(ACTION_WIDGETS, "AdaptiveActionBar", "__init__")

    assert "self.box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)" in source
    assert "self.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)" in source
    assert "self.set_propagate_natural_width(False)" in source
    assert "self.set_min_content_width(0)" in source


def test_later_groups_only_append_to_body_not_action_bar():
    group_source = _class_method_source(MAIN_WINDOW, "DetailPanel", "add_group")
    append_source = _class_method_source(MAIN_WINDOW, "DetailPanel", "_append_content")
    actions_source = _class_method_source(MAIN_WINDOW, "DetailPanel", "add_actions")

    assert "self._group(title, rows)" in group_source
    assert "self.content.append(widget)" in append_source
    assert "action_scroll" not in append_source
    assert "self._actions(buttons)" in actions_source
