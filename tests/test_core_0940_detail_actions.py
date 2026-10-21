"""Pure fake-GTK interaction checks for #940; no application startup or HTTP."""
from __future__ import annotations
from tests.gui_source import gui_source_text

import ast
from pathlib import Path
from types import SimpleNamespace as NS

from cifereca.book_detail_metadata import canonical_book_isbn, safe_detail_value


MAIN = Path(__file__).resolve().parents[1] / "src/cifereca/gui/main_window.py"


class Widget:
    def __init__(self):
        self.children = []
        self.handlers = {}
    def set_title(self, value): self.title = value
    def set_use_markup(self, value): self.use_markup = value
    def set_subtitle(self, value): self.subtitle = value
    def set_subtitle_lines(self, *_): pass
    def set_tooltip_text(self, value): self.tooltip = value
    def update_property(self, keys, values): self.accessible_label = values[0]
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
    tree = ast.parse(gui_source_text(MAIN))
    panel = next(x for x in tree.body if isinstance(x, ast.ClassDef) and x.name == "DetailPanel")
    method = next(x for x in panel.body if isinstance(x, ast.FunctionDef) and x.name == "_group")
    clipboard = NS(value=None)
    clipboard.set_content = lambda provider: (setattr(clipboard, "value", provider), True)[1]
    display = NS(get_clipboard=lambda: clipboard)
    env = {
        "Adw": NS(PreferencesGroup=Widget, ExpanderRow=Widget, ActionRow=Widget),
        "Gtk": NS(Button=Button, AccessibleProperty=NS(LABEL="label")),
        "Gdk": NS(Display=NS(get_default=lambda: display),
                  ContentProvider=NS(new_for_value=lambda text: text)),
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
    assert all(row.use_markup is False for row in expander.children)
    buttons = [[x for x in row.children if isinstance(x, Button)] for row in expander.children]
    assert len(buttons[0]) == 2
    assert len(buttons[2]) == 1  # Date is copy-only
    buttons[0][0].click()
    assert clipboard.value == "Éloïse"
    assert window._clipboard_provider == "Éloïse"
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
    # The visible count must reflect safe, rendered fields, not raw input rows.
    assert expander.subtitle == "1 champ(s) bibliographiques"
    assert clipboard.value is None
    assert events == []



def test_942_multi_valued_safe_fields_have_exact_visible_count_and_individual_copy():
    """Three public fields, two secrets: count only GTK rows actually added."""
    method, clipboard = _group()
    events, groups = [], []
    window = NS(
        service=NS(current_identity=NS(identity_id="camille")),
        toast=events.append,
        search_books=lambda *_a, **_k: events.append("unexpected-search"),
    )
    host = NS(window=window, _append_content=groups.append)
    fields = (
        NS(kind="author"), NS(kind="all"), NS(kind="author"),
        NS(kind="subject"), NS(kind="all"),
    )
    method(host, "Détail", [
        ("Auteur", "Alice Exemple"),
        ("Lien signé", "https://example.test/ebook?token=secret"),
        ("Auteur", "Bob Exemple"),
        ("Sujet", "nature"),
        ("Authentification", "Bearer private-value"),
    ], fields=fields)
    assert len(groups) == 1
    expander = groups[0].children[0]
    assert [row.title for row in expander.children] == ["Auteur", "Auteur", "Sujet"]
    # A raw source has five rows, but only three are authorized/rendered.
    assert expander.subtitle == "3 champ(s) bibliographiques"
    assert len(expander.children) == 3
    buttons = [
        [child for child in row.children if isinstance(child, Button)]
        for row in expander.children
    ]
    assert all(len(row_buttons) == 2 for row_buttons in buttons)
    assert clipboard.value is None and events == []  # no implicit copy/HTTP
    buttons[1][0].click()
    assert clipboard.value == "Bob Exemple"
    assert events == ["Copié"]


def test_942_all_private_fields_cannot_leave_an_empty_detail_group():
    """An all-rejected input must not disclose its presence as an empty expander."""
    method, clipboard = _group()
    events, groups = [], []
    window = NS(
        service=NS(current_identity=None), toast=events.append,
        search_books=lambda *_a, **_k: events.append("unexpected-search"),
    )
    host = NS(window=window, _append_content=groups.append)
    method(host, "Détail", [
        ("URL privée", "https://example.test/ebook?token=secret"),
        ("Authentification", "Bearer private-value"),
    ], fields=(NS(kind="all"), NS(kind="all")))
    # No safe bibliographic field: do not show a misleading empty "Détail"
    # section, even with a "0 champ(s)" subtitle.
    assert groups == []
    assert clipboard.value is None and events == []


def test_isbn_validator_refuses_non_bookland_ean_and_invalid_checksum():
    assert canonical_book_isbn("978-0-306-40615-7") == "9780306406157"
    assert canonical_book_isbn("4006381333931") == ""
    assert canonical_book_isbn("9780306406158") == ""
    assert canonical_book_isbn("0-306-40615-2") == "9780306406157"


def test_value_names_distinguish_repeated_authors_and_keep_full_copy():
    method, clipboard = _group()
    groups, events = [], []
    window = NS(service=NS(current_identity=None), toast=lambda *_: None,
                search_books=lambda *a, **kw: events.append((a, kw)))
    host = NS(window=window, _append_content=groups.append)
    long_value = "Long début " + "texte " * 50 + "fin distincte"
    method(host, "Détail", [("Auteur", "Alice Exemple"), ("Auteur", "Bob Exemple"),
                             ("Description", long_value)],
           fields=(NS(kind="author"), NS(kind="author"), NS(kind="copy")))
    rows = groups[0].children[0].children
    names = [row.children[0].accessible_label for row in rows]
    assert names[:2] == ["Copier Auteur : Alice Exemple", "Copier Auteur : Bob Exemple"]
    assert rows[1].children[1].accessible_label == "Rechercher en ligne : Auteur : Bob Exemple"
    assert all(child.tooltip == child.accessible_label for row in rows for child in row.children)
    assert len(names[2]) <= len("Copier Description : ") + 180
    assert names[2].endswith("fin distincte")
    assert events == []
    rows[2].children[0].click()
    assert clipboard.value == long_value and events == []
