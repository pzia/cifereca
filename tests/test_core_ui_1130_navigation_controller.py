"""P0 #1130: navigation coordination and MainWindow compatibility, headless."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS

from cifereca.gui import navigation_controller as navigation
from cifereca.gui.navigation_history import NavigationHistory, NavigationState


MAIN = Path(__file__).resolve().parents[1] / "src/cifereca/gui/main_window.py"
CONTROLLER = Path(__file__).resolve().parents[1] / "src/cifereca/gui/navigation_controller.py"


def _host():
    calls = []
    history = NavigationHistory()
    identity = NS(identity_id="alice")
    controls = NS(state=lambda: "query-alice", set_state=lambda state: calls.append(("filter", state)))
    host = NS(
        service=NS(current_identity=identity),
        search_controls=controls,
        _pages={"library": object(), "search": object()},
        _navigation_history=history,
        _restoring_navigation=False,
        nav_rows={},
        nav_list=NS(),
        _show_route=lambda route: calls.append(("show", route)),
        _update_navigation_buttons=lambda: calls.append(("buttons", None)),
    )
    # MainWindow._show_route("library") already renders Library; mirror that
    # contract instead of pretending route display is presentation-free.
    def show(route):
        calls.append(("show", route))
        if route == "library":
            renderer = getattr(host, "_render_library", None)
            if callable(renderer):
                renderer()
    host._show_route = show
    host._navigation_state = lambda route: navigation.navigation_state(host, route)
    host._restore_navigation_state = lambda state: navigation.restore_navigation_state(host, state)
    host._visit_route = lambda route: navigation.visit_route(host, route)
    return host, calls, history


def test_controller_has_no_gtk_import_or_window_dependency():
    tree = ast.parse(CONTROLLER.read_text(encoding="utf-8"))
    imports = [ast.unparse(node) for node in ast.walk(tree)
               if isinstance(node, (ast.Import, ast.ImportFrom))]
    assert not any("gi" in code or "main_window" in code for code in imports)


def test_main_window_keeps_original_facade_signatures():
    tree = ast.parse(MAIN.read_text(encoding="utf-8"))
    cls = next(node for node in tree.body
               if isinstance(node, ast.ClassDef) and node.name == "MainWindow")
    methods = {node.name: node for node in cls.body if isinstance(node, ast.FunctionDef)}
    for name, function, args in (
        ("_navigation_state", "navigation_state", ["self", "route"]),
        ("_update_navigation_buttons", "update_navigation_buttons", ["self"]),
        ("_visit_route", "visit_route", ["self", "route"]),
        ("_restore_navigation_state", "restore_navigation_state", ["self", "state"]),
        ("can_go_back", "can_go_back", ["self"]),
        ("can_go_forward", "can_go_forward", ["self"]),
        ("go_back", "go_back", ["self"]),
        ("go_forward", "go_forward", ["self"]),
        ("_nav_selected", "nav_selected", ["self", "_listbox", "row"]),
        ("navigate", "navigate", ["self", "route"]),
    ):
        method = methods[name]
        assert [arg.arg for arg in method.args.args] == args
        assert len(method.body) == 1 and isinstance(method.body[0], ast.Return)
        call = method.body[0].value
        assert isinstance(call, ast.Call)
        assert isinstance(call.func, ast.Attribute) and call.func.attr == function
        assert isinstance(call.func.value, ast.Name) and call.func.value.id == "navigation_controller"


def test_visit_records_history_and_never_dispatches_unknown_route():
    host, calls, history = _host()
    navigation.visit_route(host, "missing")
    assert history.current is None and not calls
    navigation.visit_route(host, "search")
    assert history.current == NavigationState("search", "alice", "query-alice")
    assert calls == [("show", "search"), ("buttons", None)]
    navigation.visit_route(host, "library")
    assert history.can_back() and not history.can_forward()
    navigation.visit_route(host, "library")
    assert len(history._back) == 1


def test_navigation_state_without_identity_or_filter():
    host, _, _ = _host()
    host.service.current_identity = None
    assert navigation.navigation_state(host, "library") == NavigationState("library", None, None)


def test_back_forward_restore_previous_states_exactly_once():
    host, calls, history = _host()
    first = NavigationState("library", "alice", None)
    second = NavigationState("search", "alice", None)
    history.visit(first)
    history.visit(second)
    host.service.identity_by_id = lambda ident: NS(identity_id=ident) if ident == "alice" else None
    host._render_library = lambda: calls.append(("render_library", None))
    host._render_search_results = lambda: calls.append(("render_search", None))
    navigation.go_back(host)
    assert history.current == first
    assert ("show", "library") in calls and ("render_library", None) in calls
    calls.clear()
    navigation.go_forward(host)
    assert history.current == second
    assert ("show", "search") in calls and ("render_search", None) in calls
    assert host._restoring_navigation is False


def test_restore_always_resets_reentrancy_flag_after_exception():
    host, calls, _ = _host()
    host.service.identity_by_id = lambda ident: NS(identity_id=ident)
    def fail(ident):
        raise RuntimeError("identity activation failed")
    host._activate_identity = fail
    try:
        navigation.restore_navigation_state(host, NavigationState("search", "bob"))
    except RuntimeError:
        pass
    else:
        raise AssertionError("activation error must propagate")
    assert host._restoring_navigation is False
    assert calls == [("buttons", None)]


def test_navigate_uses_selected_row_signal_without_duplicate_visit():
    host, calls, _ = _host()
    row = NS(get_name=lambda: "library")
    other = NS(get_name=lambda: "search")
    host.nav_rows = {"library": row, "search": other}
    selected = [row]
    host.nav_list.get_selected_row = lambda: selected[0]
    host.nav_list.select_row = lambda next_row: (selected.__setitem__(0, next_row),
                                                 navigation.nav_selected(host, None, next_row))
    navigation.navigate(host, "library")
    navigation.navigate(host, "search")
    navigation.navigate(host, "missing")
    assert [x for x in calls if x[0] == "show"] == [
        ("show", "library"), ("show", "search"),
    ]
    assert selected[0] is other


def test_null_selection_is_ignored_and_no_identity_is_synthesized():
    host, calls, _ = _host()
    navigation.nav_selected(host, None, None)
    assert calls == []
    row = NS(get_name=lambda: "")
    navigation.nav_selected(host, None, row)
    assert calls[0] == ("show", "library")


def test_button_sensitivity_reflects_history_state():
    host, _, history = _host()
    back, forward = [], []
    host.back_button = NS(set_sensitive=back.append)
    host.forward_button = NS(set_sensitive=forward.append)
    navigation.update_navigation_buttons(host)
    history.visit(NavigationState("library"))
    history.visit(NavigationState("search"))
    navigation.update_navigation_buttons(host)
    history.back()
    navigation.update_navigation_buttons(host)
    assert back == [False, True, False]
    assert forward == [False, False, True]
