"""Pure fake-GTK interaction checks for #940; no application startup or HTTP."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS

from cifereca.book_detail_metadata import canonical_book_isbn, safe_detail_value


MAIN = Path(__file__).resolve().parents[1] / "src/cifereca/gui/main_window.py"


class Widget:
    def __init__(self):
        self.children = []
        self.handlers = {}
    def set_title(self, *_): pass
    def set_subtitle(self, *_): pass
    def set_subtitle_lines(self, *_): pass
    def set_tooltip_text(self, *_): pass
    def set_expanded(self, *_): pass
    def get_expanded(self): return True
    def add_css_class(self, *_): pass
    def add(self, item): self.children.append(item)
    def add_row(self, item): self.children.append(item)
    def add_suffix(self, item): self.children.append(item)
    def connect(self, name, fn): self.handlers[name] = fn


class Button(Widget):
    def __init__(self, icon):
        super().__init__()
        self.icon = icon
    @classmethod
    def new_from_icon_name(cls, icon):
        return cls(icon)
    def click(self):
        self.handlers["clicked"](self)


def _group():
    tree = ast.parse(MAIN.read_text(encoding="utf-8"))
    panel = next(x for x in tree.body if isinstance(x, ast.ClassDef) and x.name == "DetailPanel")
    method = next(x for x in panel.body if isinstance(x, ast.FunctionDef) and x.name == "_group")
    clipboard = NS(value=None, set_text=lambda value: setattr(clipboard, "value", value))
    display = NS(get_clipboard=lambda: clipboard)
    env = {
        "Adw": NS(PreferencesGroup=Widget, ExpanderRow=Widget, ActionRow=Widget),
        "Gtk": NS(Button=Button),
        "Gdk": NS(Display=NS(get_default=lambda: display)),
        "detail_display_value": lambda _label, value: (value, value),
        "canonical_book_isbn": canonical_book_isbn,
        "safe_detail_value": safe_detail_value,
    }
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(MAIN), "exec"), env)
    return env["_group"], clipboard


def test_copy_is_independent_and_search_is_explicit_and_typed():
    method, clipboard = _group()
    events = []
    window = NS(
        service=NS(current_identity=NS(identity_id="alice")),
        toast=lambda message: events.append(("toast", message)),
        search_books=lambda *args, **kw: events.append(("search", args, kw)),
    )
    host = NS(window=window, _append_content=lambda widget: groups.append(widget))
    groups = []
    method(host, "Détail", [
        ("Auteur", "Éloïse"), ("ISBN", "9780306406157"),
        ("Année", "2025"), ("Sujet", "Découverte"),
    ], fields=(
        NS(kind="author"), NS(kind="isbn"), NS(kind="copy"), NS(kind="subject"),
    ))
    assert events == [] and clipboard.value is None
    expander = groups[0].children[0]
    assert len(expander.children) == 4
    buttons = [[x for x in row.children if isinstance(x, Button)] for row in expander.children]
    assert len(buttons[0]) == 2
    assert len(buttons[2]) == 1  # Date is copy-only
    buttons[0][0].click()
    assert clipboard.value == "Éloïse"
    assert events == [("toast", "Copié")]
    buttons[1][1].click()
    assert events[-1] == ("search", ("9780306406157",), {
        "field": "isbn", "online": True, "identity_id": "alice",
    })
    assert len([event for event in events if event[0] == "search"]) == 1


def test_signed_urls_and_secrets_are_not_copied_or_searched():
    method, clipboard = _group()
    events = []
    groups = []
    window = NS(service=NS(current_identity=None), toast=events.append,
                search_books=lambda *a, **kw: events.append("search"))
    host = NS(window=window, _append_content=groups.append)
    method(host, "Détail", [
        ("Notice privée", "https://example.test/?token=secret"),
        ("Auteur", "Public"),
    ], fields=(NS(kind="all"), NS(kind="author")))
    expander = groups[0].children[0]
    assert len(expander.children) == 1
    assert clipboard.value is None
    assert events == []


def test_isbn_validator_refuses_non_bookland_ean_and_invalid_checksum():
    assert canonical_book_isbn("978-0-306-40615-7") == "9780306406157"
    assert canonical_book_isbn("4006381333931") == ""
    assert canonical_book_isbn("9780306406158") == ""
    assert canonical_book_isbn("0-306-40615-2") == "9780306406157"
