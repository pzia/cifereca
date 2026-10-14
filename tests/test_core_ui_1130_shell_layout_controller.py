"""P0 #1130: shell layout stays GTK-owner scoped and idempotent."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS

from cifereca.gui.shell_layout_controller import (
    add_nav_row,
    ensure_pages_menu_rightmost,
)


MAIN = Path(__file__).resolve().parents[1] / "src/cifereca/gui/main_window.py"
OWNER = MAIN.with_name("shell_layout_controller.py")


class Widget:
    def __init__(self, **kwargs):
        self.props = kwargs
        self.children = []
        self.margins = {}

    def set_name(self, name):
        self.name = name

    def set_child(self, child):
        self.child = child

    def append(self, child):
        self.children.append(child)

    def set_margin_top(self, value):
        self.margins["top"] = value

    def set_margin_bottom(self, value):
        self.margins["bottom"] = value

    def set_margin_start(self, value):
        self.margins["start"] = value

    def set_margin_end(self, value):
        self.margins["end"] = value

    def set_pixel_size(self, value):
        self.pixel_size = value

    def set_hexpand(self, value):
        self.hexpand = value


class Image(Widget):
    @classmethod
    def new_from_icon_name(cls, icon_name):
        return cls(icon_name=icon_name)


Gtk = NS(
    ListBoxRow=Widget, Box=Widget, Image=Image, Label=Widget,
    Orientation=NS(HORIZONTAL=1),
)


class NativeBox:
    def __init__(self, sequence):
        self.sequence = list(sequence)
        self.calls = []

    def get_last_child(self):
        return self.sequence[-1] if self.sequence else None

    def remove(self, item):
        self.calls.append(("remove", item))
        self.sequence.remove(item)

    def append(self, item):
        self.calls.append(("append", item))
        self.sequence.append(item)


class NativeMenu:
    def __init__(self, parent):
        self.parent = parent

    def get_parent(self):
        return self.parent


def test_controller_is_headless_and_public_mainwindow_facades_are_stable():
    imports = [ast.unparse(node) for node in ast.walk(ast.parse(OWNER.read_text()))
               if isinstance(node, (ast.Import, ast.ImportFrom))]
    assert not any(line == "import gi" or line.startswith("from gi ")
                   or "main_window" in line for line in imports)
    tree = ast.parse(MAIN.read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef)
               and n.name == "MainWindow")
    methods = {n.name: n for n in cls.body if isinstance(n, ast.FunctionDef)}
    add = methods["_add_nav_row"]
    assert [a.arg for a in add.args.args] == ["self", "route", "label", "icon_name"]
    assert "shell_layout_controller.add_nav_row" in ast.unparse(add)
    assert "Gtk=Gtk" in ast.unparse(add)
    menu = methods["_ensure_pages_menu_rightmost"]
    assert [a.arg for a in menu.args.args] == ["self"]
    assert "shell_layout_controller.ensure_pages_menu_rightmost" in ast.unparse(menu)


def test_nav_row_uses_exact_route_label_icon_dimensions_and_spacing():
    rows, box = {}, Widget()
    window = NS(nav_rows=rows, nav_list=box)
    add_nav_row(window, "library", "Bibliothèque", "folder-symbolic", Gtk=Gtk)
    row = rows["library"]
    assert box.children == [row]
    assert row.name == "library"
    line = row.child
    assert line.props == {"orientation": 1, "spacing": 10}
    assert line.margins == {"top": 7, "bottom": 7, "start": 8, "end": 8}
    icon, title = line.children
    assert icon.props["icon_name"] == "folder-symbolic"
    assert icon.pixel_size == 18
    assert title.props == {"label": "Bibliothèque", "xalign": 0}
    assert title.hexpand is True


def test_duplicate_route_does_not_replace_nav_row_or_append_again():
    window = NS(nav_rows={}, nav_list=Widget())
    add_nav_row(window, "search", "Recherche", "find-symbolic", Gtk=Gtk)
    first = window.nav_rows["search"]
    add_nav_row(window, "search", "Autre", "warning-symbolic", Gtk=Gtk)
    assert window.nav_rows["search"] is first
    assert len(window.nav_list.children) == 1


def test_closed_window_cannot_rebuild_detached_navigation_widgets():
    window = NS(_closed=True, nav_rows={}, nav_list=Widget())
    add_nav_row(window, "library", "Bibliothèque", "folder-symbolic", Gtk=Gtk)
    assert window.nav_rows == {}
    assert window.nav_list.children == []


def test_simple_header_box_keeps_menu_rightmost_without_duplicates():
    menu = object()
    box = NS(children=[menu, "identity", "account", "statistics"])
    window = NS(_header_controls=box, pages_menu_button=menu)
    ensure_pages_menu_rightmost(window)
    assert box.children == ["identity", "account", "statistics", menu]
    ensure_pages_menu_rightmost(window)
    assert box.children == ["identity", "account", "statistics", menu]


def test_missing_header_controls_or_pages_menu_does_not_mutate_container():
    ensure_pages_menu_rightmost(NS())
    box = NS(children=["identity"])
    ensure_pages_menu_rightmost(NS(_header_controls=box))
    assert box.children == ["identity"]
    ensure_pages_menu_rightmost(NS(pages_menu_button=object()))
    assert box.children == ["identity"]


def test_native_header_noop_when_menu_already_rightmost():
    menu = object()
    box = NativeBox(["identity", "account", menu])
    window = NS(_header_controls=box, pages_menu_button=menu)
    ensure_pages_menu_rightmost(window)
    assert box.sequence == ["identity", "account", menu]
    assert box.calls == []


def test_native_header_moves_menu_from_same_parent_to_end():
    box = NativeBox([])
    menu = NativeMenu(box)
    box.sequence = [menu, "identity", "account"]
    window = NS(_header_controls=box, pages_menu_button=menu)
    ensure_pages_menu_rightmost(window)
    assert box.sequence == ["identity", "account", menu]
    assert box.calls == [("remove", menu), ("append", menu)]
    box.calls.clear()
    ensure_pages_menu_rightmost(window)
    assert box.calls == []


def test_native_header_appends_menu_when_it_belongs_to_another_parent():
    other = NativeBox([])
    menu = NativeMenu(other)
    box = NativeBox(["identity"])
    window = NS(_header_controls=box, pages_menu_button=menu)
    ensure_pages_menu_rightmost(window)
    assert box.sequence == ["identity", menu]
    assert box.calls == [("append", menu)]


def test_closed_window_never_reparents_header_menu_or_accesses_buttons():
    menu = object()
    box = NativeBox([menu, "identity"])
    window = NS(_closed=True, _header_controls=box, pages_menu_button=menu)
    ensure_pages_menu_rightmost(window)
    assert box.sequence == [menu, "identity"]
    assert box.calls == []