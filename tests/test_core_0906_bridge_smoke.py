"""Offline #906 smoke using actual Core and Jeunesse method AST, not copies.

GTK is not imported. This test checks the public host-to-plugin interaction
and never performs HTTP or calls a provider.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, replace
from tests.gui_source import gui_source_text
from pathlib import Path
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[1]
CORE_FILE = ROOT / "src/cifereca/gui/main_window.py"
YOUTH_FILE = ROOT / "src/plugins/youth-catalog/youth_page.py"


@dataclass(frozen=True)
class UiBookDetailAction:
    id: str
    title: str
    icon_name: str = ""
    uri: str = ""
    callback: object = None


@dataclass(frozen=True)
class Presentation:
    actions: tuple = ()


@dataclass(frozen=True)
class QueryState:
    text: str = ""
    field: str = "all"
    scope: str = "all"
    providers: tuple = ()
    types: tuple = ()
    languages: tuple = ()


def _text(value):
    return " ".join(str(value or "").strip().split())


def _method(path, class_name, name, env):
    tree = ast.parse(gui_source_text(path))
    cls = next(item for item in ast.walk(tree)
               if isinstance(item, ast.ClassDef) and item.name == class_name)
    fn = next(item for item in cls.body
              if isinstance(item, ast.FunctionDef) and item.name == name)
    fn.decorator_list = []
    if class_name == "MainWindow":
        # Only replace the import of a pure query model with its fake.
        fn.body = [node for node in fn.body
                   if not (isinstance(node, ast.ImportFrom)
                           and node.module == "query_model")]
    exec(compile(ast.fix_missing_locations(
        ast.Module(body=[fn], type_ignores=[])), str(path), "exec"), env)
    return env[name]


def _setup(active="alice", selected="alice"):
    env = {
        "replace": replace, "UiBookDetailAction": UiBookDetailAction,
        "detail_presentation": lambda _book: Presentation(), "_text": _text,
        "QueryState": QueryState,
        "FIELDS": ("all", "title", "author", "isbn", "subject",
                   "series", "collection", "language"),
    }
    core_type = type("Core", (), {
        "search_books": _method(CORE_FILE, "MainWindow", "search_books", env),
    })
    host_type = type("Host", (), {
        # This AST fixture must retain the host's real lifecycle guard:
        # extracting search_books alone misses its instance dependency.
        "_window_alive": _method(CORE_FILE, "_UiHost", "_window_alive", env),
        "search_books": _method(CORE_FILE, "_UiHost", "search_books", env),
    })
    youth_type = type("Youth", (), {
        "selected": _method(YOUTH_FILE, "YouthView", "selected", env),
        "_search_selected_book": _method(YOUTH_FILE, "YouthView", "_search_selected_book", env),
        "_search_library": _method(YOUTH_FILE, "YouthView", "_search_library", env),
    })
    events, shown, toasts, empty = [], [], [], []
    core = core_type()
    core.service = NS(current_identity=NS(identity_id=active))
    core.search_controls = NS(
        set_state=lambda state, notify: events.append(("state", state, notify)))
    core.navigate = lambda route: events.append(("route", route))
    core.run_search = lambda online: events.append(("run", online))
    host = host_type()
    host.window = core
    host.toast = toasts.append
    youth = youth_type()
    youth.rows = {}
    youth.host = host
    youth.identity_id = lambda: selected
    youth.master = NS(
        show_detail=shown.append, empty_detail=lambda: empty.append(True))
    return youth, events, shown, toasts, empty


def test_906_public_host_bridge_runs_one_local_title_search_only_on_click():
    view, events, shown, toasts, _empty = _setup()
    title = "C'est quoi, l'écologie ?"
    view.rows["edition"] = NS(title=title, isbn="9780306406157")
    view.selected(NS(key="edition"))
    assert not events
    assert len(shown[0].actions) == 1
    action = shown[0].actions[0]
    assert (action.id, action.title, action.icon_name) == (
        "search-library", "Rechercher en bibliothèque", "system-search-symbolic")
    action.callback()
    assert events == [
        ("state", QueryState(text=title, field="title", scope="all"), False),
        ("route", "search"), ("run", False),
    ]
    assert not toasts


def test_906_public_host_rejects_cross_identity_without_navigation():
    view, events, shown, toasts, _empty = _setup(active="alice", selected="bob")
    view.rows["edition"] = NS(title="Livre autre profil")
    view.selected(NS(key="edition"))
    shown[0].actions[0].callback()
    assert not events
    assert len(toasts) == 1 and "profil" in toasts[0]


def test_906_does_not_claim_two_homonymous_editions_are_same_isbn():
    view, events, shown, _toasts, _empty = _setup()
    view.rows = {
        "a": NS(title="Même titre", isbn="9780306406157"),
        "b": NS(title="Même titre", isbn="9780140328721"),
    }
    view.selected(NS(key="a"))
    old_action = shown[-1].actions[0]
    old_action.callback()
    view.selected(NS(key="b"))
    current_action = shown[-1].actions[0]
    assert len([x for x in events if x[0] == "run"]) == 1
    # #1609 prevents a stale action for edition A from searching B.
    old_action.callback()
    assert len([x for x in events if x[0] == "run"]) == 1
    current_action.callback()
    assert len([x for x in events if x[0] == "run"]) == 2
    assert [x[1].field for x in events if x[0] == "state"] == ["title", "title"]


def test_906_empty_selection_and_optional_host_remain_compatible():
    view, events, shown, _toasts, empty = _setup()
    view.selected(None)
    assert empty == [True]
    view.rows["a"] = NS(title="Le livre")
    view.host = NS()
    view.selected(NS(key="a"))
    assert shown[-1].actions == ()
    assert not events


def test_906_invalid_query_cannot_change_search_controls():
    view, events, _shown, _toasts, _empty = _setup()
    for value in ("", "x", "q" * 513, None):
        assert view.host.search_books(value, identity_id="alice") is False
    assert view.host.search_books(
        "Un livre", field="bogus", identity_id="alice") is False
    assert not events



def test_959_public_bridge_without_isbn_mapping_keeps_legacy_title_search():
    """The extracted method must not depend on a module-only Mapping import."""
    view, events, shown, toasts, _empty = _setup()
    view.rows = {
        "old": NS(title="Livre sans notice", isbn="9780306406157"),
        "invalid": NS(title="Livre sans identifiants", identifiers=[]),
        "none": NS(title="Livre avec identifiants absents", identifiers=None),
    }
    for key in view.rows:
        view.selected(NS(key=key))
        assert [action.id for action in shown[-1].actions] == ["search-library"]
    assert not events
    assert not toasts
