"""P0 #1130: safe execution boundary for public plugin menu actions."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from cifereca.gui.plugin_action_dispatch_controller import invoke_ui_action
from cifereca.plugin_api import UiActionContribution


ROOT = Path(__file__).resolve().parents[1] / "src/cifereca/gui"
OWNER = ROOT / "plugin_action_dispatch_controller.py"
MAIN = ROOT / "main_window.py"


def _window():
    events = []
    window = NS(
        _closed=False,
        shell_menu=NS(popdown=lambda: events.append("dismiss")),
        show_error=lambda message, title: events.append(("error", message, title)),
    )
    return window, events


def _action(callback):
    return UiActionContribution(
        id="test-action",
        title="Action test",
        callback=callback,
        icon_name="system-run-symbolic",
    )


def test_no_gui_or_mainwindow_import_and_public_compatibility_facade():
    module = ast.parse(OWNER.read_text(encoding="utf-8"))
    imports = [ast.unparse(n) for n in ast.walk(module)
               if isinstance(n, (ast.Import, ast.ImportFrom))]
    assert not any("import gi" in s or "main_window" in s for s in imports)
    main = ast.parse(MAIN.read_text(encoding="utf-8"))
    owner = next(n for n in main.body
                 if isinstance(n, ast.ClassDef) and n.name == "MainWindow")
    method = next(n for n in owner.body
                  if isinstance(n, ast.FunctionDef) and n.name == "_invoke_ui_action")
    assert [a.arg for a in method.args.args] == ["self", "action", "host"]
    assert "plugin_action_dispatch_controller.invoke_ui_action" in ast.unparse(method)


def test_action_receives_exact_public_host_after_menu_is_dismissed():
    window, events = _window()
    host = object()
    action = _action(lambda passed: events.append(("callback", passed)))
    invoke_ui_action(window, action, host)
    assert events == ["dismiss", ("callback", host)]


def test_closure_captures_no_new_plugin_or_service_lookups():
    window, events = _window()
    action = _action(lambda host: events.append("invoked"))
    invoke_ui_action(window, action, NS())
    assert events == ["dismiss", "invoked"]
    assert not hasattr(window, "plugin_manager")
    assert not hasattr(window, "service")


def test_closed_window_never_accesses_menu_action_or_host():
    window = NS(_closed=True)
    action = NS(callback=Mock(side_effect=AssertionError("should not call")))
    invoke_ui_action(window, action, object())
    action.callback.assert_not_called()


def test_pending_click_after_close_does_not_pop_down_detached_menu():
    window, events = _window()
    action = _action(lambda host: events.append("callback"))
    window._closed = True
    invoke_ui_action(window, action, NS())
    assert events == []


def test_regular_plugin_failure_reports_existing_public_action_error():
    window, events = _window()
    def failing(_host):
        events.append("callback")
        raise ValueError("Plugin indisponible")
    invoke_ui_action(window, _action(failing), NS())
    assert events == [
        "dismiss", "callback", ("error", "Plugin indisponible", "Action impossible"),
    ]


def test_failed_callback_that_closes_window_cannot_present_dialog_after_teardown():
    window, events = _window()
    def failing(_host):
        events.append("callback")
        window._closed = True
        raise RuntimeError("late private failure")
    invoke_ui_action(window, _action(failing), NS())
    assert events == ["dismiss", "callback"]


def test_action_can_close_window_normally_without_error():
    window, events = _window()
    def close(_host):
        window._closed = True
        events.append("closed")
    invoke_ui_action(window, _action(close), NS())
    assert events == ["dismiss", "closed"]


def test_action_return_value_is_not_interpreted_as_a_new_navigation():
    window, events = _window()
    invoke_ui_action(window, _action(lambda _host: "plugin-route"), NS())
    assert events == ["dismiss"]


def test_action_error_uses_exact_exception_string_not_repr():
    window, events = _window()
    invoke_ui_action(window, _action(
        lambda _host: (_ for _ in ()).throw(KeyError("unknown-route")),
    ), NS())
    assert events[-1] == ("error", "'unknown-route'", "Action impossible")


def test_independent_action_invocations_are_not_coalesced():
    window, events = _window()
    action = _action(lambda _host: events.append("call"))
    invoke_ui_action(window, action, NS())
    invoke_ui_action(window, action, NS())
    assert events == ["dismiss", "call", "dismiss", "call"]


def test_failure_in_menu_dismissal_does_not_execute_plugin_callback():
    window, events = _window()
    window.shell_menu.popdown = Mock(side_effect=RuntimeError("detached"))
    action = _action(lambda _host: events.append("callback"))
    with pytest.raises(RuntimeError, match="detached"):
        invoke_ui_action(window, action, NS())
    assert events == []
