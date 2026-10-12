from __future__ import annotations

import ast
from tests.gui_source import gui_source_text
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace as NS


ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "src/cifereca/gui/main_window.py"
MODELS = ROOT / "src/cifereca/plugin_api/models.py"


@dataclass
class QueryState:
    text: str = ""
    field: str = "all"
    scope: str = "all"
    providers: tuple = ()
    types: tuple = ()
    languages: tuple = ()


def _route():
    tree = ast.parse(gui_source_text(MAIN))
    cls = next(x for x in tree.body if isinstance(x, ast.ClassDef) and x.name == "MainWindow")
    method = next(x for x in cls.body if isinstance(x, ast.FunctionDef) and x.name == "search_books")
    # Stub just the pure query-model import: no GTK, service or network involved.
    method.body = [
        x for x in method.body
        if not (isinstance(x, ast.ImportFrom) and x.module == "query_model")
    ]
    env = {"QueryState": QueryState, "FIELDS": (
        "all", "title", "author", "isbn", "subject", "series", "collection", "language",
    )}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])), str(MAIN), "exec"), env)
    return env["search_books"]


def _window():
    events = []
    current = NS(identity_id="alice")
    controls = NS(set_state=lambda state, notify: events.append(("state", state, notify)))
    window = NS(service=NS(current_identity=current), search_controls=controls,
                navigate=lambda route: events.append(("navigate", route)),
                run_search=lambda online: events.append(("run", online)))
    return window, events


def test_routing_once_local_and_online_are_distinct():
    route = _route()
    window, events = _window()
    assert route(window, "Écologie", field="title", identity_id="alice")
    assert [x[0] for x in events] == ["state", "navigate", "run"]
    assert events[0][1] == QueryState(text="Écologie", field="title", scope="all")
    assert events[0][2] is False
    assert events[-1] == ("run", False)
    events.clear()
    assert route(window, "9780306406157", field="isbn", online=True)
    assert events[-1] == ("run", True)
    assert len([x for x in events if x[0] == "run"]) == 1


def test_routing_rejects_identity_mismatch_invalid_field_and_text_without_mutation():
    route = _route()
    window, events = _window()
    for kwargs in (
        {"identity_id": "bob"}, {"field": "unsupported"}, {"online": "true"},
    ):
        assert route(window, "Valid title", **kwargs) is False
    for query in ("", "x", "a" * 513, None):
        assert route(window, query) is False
    assert events == []


def test_public_contract_remains_optional_and_host_delegates():
    tree = ast.parse(MODELS.read_text(encoding="utf-8"))
    classes = {node.name: node for node in tree.body if isinstance(node, ast.ClassDef)}
    assert "UiSearchNavigationHost" in classes
    assert [base.id for base in classes["UiSearchNavigationHost"].bases] == ["UiPageHost", "Protocol"]
    assert all(node.name != "search_books" for node in classes["UiPageHost"].body if isinstance(node, ast.FunctionDef))
    main = ast.parse(gui_source_text(MAIN))
    host = next(x for x in ast.walk(main) if isinstance(x, ast.ClassDef) and x.name == "_UiHost")
    assert any(isinstance(x, ast.FunctionDef) and x.name == "search_books" for x in host.body)
