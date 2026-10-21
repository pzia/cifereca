"""P1 #942: only safe, actually rendered bibliographic rows count.

Run the real shared DetailPanel method with small fake GTK widgets. The test
makes no GTK display, HTTP request or clipboard access during rendering.
"""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS

from cifereca.book_detail_metadata import safe_detail_value


MAIN = Path(__file__).resolve().parents[1] / "src/cifereca/gui/detail_panel.py"


class Widget:
    def __init__(self):
        self.children = []
        self.handlers = {}
        self.title = ""
        self.subtitle = ""

    def set_title(self, value):
        self.title = value

    def set_subtitle(self, value):
        self.subtitle = value

    def set_subtitle_lines(self, _value):
        pass

    def set_tooltip_text(self, value):
        self.tooltip = value

    def update_property(self, keys, values):
        self.accessible_label = values[0]

    def set_expanded(self, value):
        self.expanded = value

    def get_expanded(self):
        return getattr(self, "expanded", False)

    def add_css_class(self, _name):
        pass

    def connect(self, signal, callback):
        self.handlers[signal] = callback

    def add(self, item):
        self.children.append(item)

    def add_row(self, item):
        self.children.append(item)

    def add_suffix(self, item):
        self.children.append(item)


class Button(Widget):
    @classmethod
    def new_from_icon_name(cls, _name):
        return cls()

    def click(self):
        self.handlers["clicked"](self)


def _render_detail(pairs, kinds):
    tree = ast.parse(MAIN.read_text(encoding="utf-8"))
    panel = next(node for node in tree.body
                 if isinstance(node, ast.ClassDef) and node.name == "DetailPanel")
    method = next(node for node in panel.body
                  if isinstance(node, ast.FunctionDef) and node.name == "_group")
    clipboard = NS(value=None)
    clipboard.set_content = lambda provider: (setattr(clipboard, "value", provider), True)[1]
    display = NS(get_clipboard=lambda: clipboard)
    env = {
        "Adw": NS(PreferencesGroup=Widget, ExpanderRow=Widget, ActionRow=Widget),
        "Gtk": NS(Button=Button, AccessibleProperty=NS(LABEL="label")),
        "Gdk": NS(Display=NS(get_default=lambda: display),
                  ContentProvider=NS(new_for_value=lambda text: text)),
        "safe_detail_value": safe_detail_value,
        "detail_display_value": lambda _label, value: (value, value),
        "canonical_book_isbn": lambda value: value,
    }
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(MAIN), "exec"), env)
    groups = []
    actions = []
    window = NS(
        service=NS(current_identity=NS(identity_id="profile-a")),
        search_books=lambda *args, **kwargs: actions.append((args, kwargs)),
        toast=lambda _message: None,
    )
    host = NS(window=window, _append_content=groups.append)
    env["_group"](host, "Détail", pairs, fields=tuple(NS(kind=kind) for kind in kinds))
    return groups, actions, clipboard


def test_mixed_public_private_metadata_counts_only_visible_rows():
    groups, actions, clipboard = _render_detail(
        [("Auteur", "Alice"), ("Notice interne", "https://example.test/?token=secret"),
         ("Auteur", "Bob"), ("Authentification", "Bearer secret-value"),
         ("Sujet", "nature")],
        ["author", "all", "author", "all", "subject"],
    )
    assert len(groups) == 1
    expander = groups[0].children[0]
    assert expander.title == "Détail"
    assert expander.subtitle == "3 champ(s) bibliographiques"
    assert [(row.title, row.subtitle) for row in expander.children] == [
        ("Auteur", "Alice"), ("Auteur", "Bob"), ("Sujet", "nature"),
    ]
    assert actions == [] and clipboard.value is None
    # Each bibliographic value retains its own copy callback, even with
    # repeated labels (two different authors).
    bob_copy = expander.children[1].children[0]
    bob_copy.click()
    assert clipboard.value == "Bob"
    assert windowless_no_search(actions)


def windowless_no_search(actions):
    return actions == []


def test_one_visible_row_never_counts_rejected_links():
    groups, _, _ = _render_detail(
        [("Notice privée", "https://example.test/?token=secret"), ("Auteur", "Alice")],
        ["all", "author"],
    )
    assert len(groups) == 1
    expander = groups[0].children[0]
    assert expander.subtitle == "1 champ(s) bibliographiques"
    assert len(expander.children) == 1


def test_all_rejected_metadata_does_not_create_empty_expander():
    groups, actions, clipboard = _render_detail(
        [("Notice privée", "https://example.test/?token=secret"),
         ("Authorization", "Bearer secret-value")],
        ["all", "all"],
    )
    assert groups == [] and actions == [] and clipboard.value is None
