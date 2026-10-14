"""P0 #1130: identity activation is transactional for GUI identity scope.

Uses real headless controller (no Gtk import), with fake service and runner.
Tests that a callback from Alice cannot repaint Bob or a newer Alice.
"""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.gui.identity_activation_controller import activate_identity
from cifereca.task_queue import TaskPriority


ROOT = Path(__file__).resolve().parents[1] / "src/cifereca/gui"
MAIN = ROOT / "main_window.py"
OWNER = ROOT / "identity_activation_controller.py"


class Runner:
    def __init__(self, events):
        self.events = events
        self.submitted = []

    def submit(self, title, work, done, **kwargs):
        self.events.append(("submit", title, kwargs))
        self.submitted.append(NS(title=title, work=work, done=done, kwargs=kwargs))


def _page(name, events):
    return NS(
        clear_rows=lambda: events.append((name, "clear")),
        detail=NS(empty=lambda title, message:
                  events.append((name, "empty", title, message)),
    )


def _window(*, widgets=True, route="library", current="alice"):
    events = []
    known = {
        "alice": NS(identity_id="alice", label="Alice"),
        "bob": NS(identity_id="bob", label="Bob"),
    }
    service = NS(
        current_identity=known[current] if current else None,
        last_history=["history-private"],
        last_bookings=["booking-private"],
    )
    service.identity_by_id = known.get

    def select_identity(ident):
        events.append(("select", ident))
        service.current_identity = known[ident]

    service.select_identity = select_identity
    service.refresh_identity_loans = lambda *, force, identity_id: (
        events.append(("work", identity_id, force)) or [f"loan-{identity_id}"]
    )
    window = NS(
        _closed=False,
        service=service,
        view_stack=NS(get_visible_child_name=lambda: route),
        runner=Runner(events),
        _identity_context_generation=7,
        _account_context_generation=11,
        _cached_book_request_generation=3,
        _cached_book_context="Alice private cache",
        _pending_library_selection_key="shared-sha",
        _pending_library_selection_identity_id="alice",
        _pending_library_selection_generation=7,
        _search_generation=20,
        _search_detail_request_generation=4,
        _search_local_objects={"alice": object()},
        search_objects={"alice": object()},
        _remote_search_results=["private remote"],
        _search_mode="online",
        _remote_query_key="private remote key",
        _selected_search_id="private selected item",
        history_objects={"alice": object()},
        booking_objects={"alice": object()},
        _history_all_rows=["private history"],
        _history_rows=["private history"],
        _history_loaded_count=1,
        _history_append_scheduled=True,
        _library_entries_cache=["private library"],
        _recent_entries_cache=["private recent"],
        _recent_objects={"alice": object()},
    )
    window._dispose_maintenance_lines = lambda: events.append(("dispose",))
    window._refresh_tools_page = lambda: events.append(("tools",))
    window._populate_accounts = lambda: events.append(("accounts",))
    window._render_dashboard = lambda: events.append(("dashboard",))
    window._populate_account_dropdown = lambda identity: events.append(("delegation", identity))

    def render_library(rows):
        assert window._pending_library_selection_key is None
        assert window._pending_library_selection_identity_id is None
        assert window._pending_library_selection_generation is None
        assert window._identity_context_generation >= 8
        events.append(("library", list(rows)))

    window._render_library = render_library
    window._identity_request_current = lambda ident, generation: (
        service.current_identity is not None
        and service.current_identity.identity_id == ident
        and generation == window._identity_context_generation
    )
    window._populate_loans = lambda rows: events.append(("loans", rows))
    window.refresh_library = lambda force: events.append(("refresh-library", force))
    window.refresh_recent = lambda: events.append(("refresh-recent",))
    window.refresh_history = lambda force: events.append(("refresh-history", force))
    window.refresh_bookings = lambda force: events.append(("refresh-bookings", force))
    window._activity_result = lambda message: events.append(("activity", message))

    if widgets:
        for name in ("search", "history", "booking", "recent", "library"):
            setattr(window, f"{name}_page", _page(name, events))

    return window, events


def test_headless_owner_and_mainwindow_facade_are_compatible():
    source = OWNER.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = [ast.unparse(node) for node in ast.walk(tree)
               if isinstance(node, (ast.Import, ast.ImportFrom))]
    assert not any("import gi" in entry or "task_runner" in entry
                   or "main_window" in entry for entry in imports)
    main = ast.parse(MAIN.read_text(encoding="utf-8"))
    owner = next(node for node in main.body
                 if isinstance(node, ast.ClassDef) and node.name == "MainWindow")
    facade = next(node for node in owner.body
                  if isinstance(node, ast.FunctionDef) and node.name == "_activate_identity")
    assert [arg.arg for arg in facade.args.args] == ["self", "identity_id"]
    assert "identity_activation_controller.activate_identity(self, identity_id)" in ast.unparse(facade)


def test_unknown_identity_rejected_before_any_widget_or_service_mutation():
    window = NS(
        _closed=False,
        service=NS(identity_by_id=lambda identity: None),
    )
    with pytest.raises(ValueError, match="Identité inconnue"):
        activate_identity(window, "not-found")


def test_closed_window_ignores_late_activation_without_service_access():
    window = NS(_closed=True)
    activate_identity(window, "bob")


def test_identical_identity_does_not_submit_or_dispose_views():
    window, events = _window()
    activate_identity(window, "alice")
    assert events == []
    assert window.runner.submitted == []
    assert window._identity_context_generation == 7


def test_transition_invalidates_every_generation_and_private_model():
    window, events = _window()
    activate_identity(window, "bob")
    assert window.service.current_identity.identity_id == "bob"
    assert window._identity_context_generation == 8
    assert window._account_context_generation == 12
    assert window._cached_book_request_generation == 4
    assert window._cached_book_context is None
    assert window._search_generation == 21
    assert window._search_detail_request_generation == 5
    assert window._search_local_objects == {}
    assert window.search_objects == {}
    assert window._remote_search_results == []
    assert window._search_mode == "local"
    assert window._remote_query_key is None
    assert window._selected_search_id is None
    assert window.service.last_history == []
    assert window.service.last_bookings == []
    assert window.history_objects == {}
    assert window.booking_objects == {}
    assert window._history_all_rows == []
    assert window._history_rows == []
    assert window._history_loaded_count == 0
    assert window._history_append_scheduled is False
    assert window._library_entries_cache == []
    assert window._recent_entries_cache == []
    assert window._recent_objects == {}
    assert events.index(("select", "bob")) < events.index(("accounts",))
    assert events.index(("dispose",)) < events.index(("dashboard",))


def test_old_search_and_secondary_details_are_cleared_immediately():
    window, events = _window()
    activate_identity(window, "bob")
    for page in ("search", "history", "booking", "recent"):
        assert (page, "clear") in events
    assert (
        "search", "empty", "Nouvelle identité",
        "Lancez une recherche dans le connu.",
    ) in events
    assert (
        "recent", "empty", "Nouvelle identité",
        "Chargement des récupérations…",
    ) in events
    assert (
        "library", "empty", "Nouvelle identité",
        "Chargement de la bibliothèque…",
    ) in events
    assert ("delegation", "bob") in events
    assert ("library", []) in events
    assert events.index(("library", [])) < next(
        i for i, event in enumerate(events) if event[0] == "submit"
    )


def test_switch_without_instantiated_pages_is_supported():
    window, events = _window(widgets=False)
    activate_identity(window, "bob")
    assert ("select", "bob") in events
    assert ("library", []) not in events
    assert len(window.runner.submitted) == 1


def test_identity_work_is_scoped_and_priority_preserved():
    window, events = _window()
    activate_identity(window, "bob")
    task = window.runner.submitted[0]
    assert task.title == "Chargement de Bob…"
    assert task.kwargs["priority"] is TaskPriority.USER_ACTION
    assert task.kwargs["kind"] == "identity"
    assert task.work() == ["loan-bob"]
    assert ("work", "bob", False) in events


def test_current_callback_renders_loans_and_refreshes_only_active_route():
    for route, expected in [
        ("history", ("refresh-history", False)),
        ("bookings", ("refresh-bookings", False)),
        ("library", None),
    ]:
        window, events = _window(route=route)
        activate_identity(window, "bob")
        window.runner.submitted[0].done(["loan-bob"])
        assert ("loans", ["loan-bob"]) in events
        assert ("refresh-library", False) in events
        assert ("refresh-recent",) in events
        assert ("activity", "Identité chargée : Bob") in events
        if expected is not None:
            assert expected in events
        else:
            assert not any(evt[0] in {"refresh-history", "refresh-bookings"} for evt in events)


def test_previous_identity_callback_cannot_repaint_current_identity():
    window, events = _window()
    activate_identity(window, "bob")
    old = window.runner.submitted[-1]
    activate_identity(window, "alice")
    events.clear()
    old.done(["private bob loan"])
    assert events == []
    window.runner.submitted[-1].done(["current alice loan"])
    assert ("loans", ["current alice loan"]) in events
    assert ("activity", "Identité chargée : Alice") in events


def test_a_b_a_old_callback_rejected_even_when_identity_id_matches():
    window, events = _window()
    activate_identity(window, "bob")
    activate_identity(window, "alice")
    stale = window.runner.submitted[-1]
    activate_identity(window, "bob")
    activate_identity(window, "alice")
    events.clear()
    stale.done(["private stale alice"])
    assert events == []
    window.runner.submitted[-1].done(["current alice"])
    assert ("loans", ["current alice"]) in events


def test_callback_after_close_never_accesses_disposed_service_or_views():
    window, events = _window()
    activate_identity(window, "bob")
    late = window.runner.submitted[-1]
    events.clear()
    window._closed = True
    window.service = None
    late.done(["private result"])
    assert events == []


def test_identity_switch_on_tools_refreshes_tools_exactly_once():
    window, events = _window(route="tools")
    activate_identity(window, "bob")
    assert events.count(("tools",)) == 1
    assert events.index(("dispose",)) < events.index(("tools",))
    assert len(window.runner.submitted) == 1


def test_identity_switch_does_not_rebuild_identity_dropdown_from_its_notification():
    window, events = _window()
    activate_identity(window, "bob")
    assert events.count(("accounts",)) == 1
    assert events.count(("delegation", "bob")) == 1


def test_callback_identity_missing_or_stale_generation_is_noop():
    window, events = _window()
    activate_identity(window, "bob")
    task = window.runner.submitted[-1]
    events.clear()
    window.service.current_identity = None
    task.done(["private"])
    assert events == []
    window.service.current_identity = NS(identity_id="bob")
    window._identity_context_generation += 1
    task.done(["private"])
    assert events == []
