"""P0 #1132 — GTK startup cannot resume after its owner is closed."""
from __future__ import annotations

import ast
import logging
import time
from pathlib import Path
from types import SimpleNamespace as NS


MAIN = Path(__file__).resolve().parents[1] / "src/cifereca/gui/main_window.py"


def _method(name):
    tree = ast.parse(MAIN.read_text(encoding="utf-8"))
    owner = next(node for node in tree.body
                 if isinstance(node, ast.ClassDef) and node.name == "MainWindow")
    method = next(node for node in owner.body
                  if isinstance(node, ast.FunctionDef) and node.name == name)
    future = ast.ImportFrom(
        module="__future__", names=[ast.alias(name="annotations")], level=0,
    )
    scheduled = []
    ns = {
        "_TRACE_LOG": logging.getLogger("startup-close-tests"),
        "GLib": NS(idle_add=lambda callback: scheduled.append(callback)),
        "time": time,
        "TaskPriority": NS(PROVIDER_SYNC=30),
    }
    exec(compile(ast.fix_missing_locations(ast.Module(
        body=[future, method], type_ignores=[],
    )), str(MAIN), "exec"), ns)
    return ns[name], scheduled


def _host(*, activate=None, refresh=None):
    calls = []
    host = NS(
        _closed=False,
        ui_perf=NS(record=lambda *args: calls.append(("perf", args))),
        diagnostics=NS(add=lambda *args, **kwargs: calls.append(("diagnostic", args))),
    )
    host.plugin_manager = NS(
        activate=activate if activate is not None else lambda: calls.append(("activate",)),
    )
    host.service = NS(
        attach_plugin_manager=lambda _manager: calls.append(("attach",)),
    )
    host._plugins_changed = (
        refresh if refresh is not None else lambda: calls.append(("refresh",))
    )
    host.connect_configured_sources = lambda: calls.append(("connect",))
    return host, calls


def test_deferred_startup_does_not_activate_plugins_after_window_closed():
    run, queued = _method("activate_startup_plugins")
    host, calls = _host()
    host._closed = True
    assert run(host) is False
    assert calls == []
    assert queued == []


def test_window_closed_during_plugin_activate_skips_service_attach_and_network():
    run, queued = _method("activate_startup_plugins")
    host, calls = _host()
    def activate():
        calls.append(("activate",))
        host._closed = True
    host.plugin_manager.activate = activate
    assert run(host) is False
    assert [event[0] for event in calls] == ["activate", "perf"]
    assert queued == []


def test_plugin_refresh_failure_still_schedules_provider_connection_when_open():
    run, queued = _method("activate_startup_plugins")
    host, calls = _host()
    def bad_refresh():
        calls.append(("refresh",))
        raise RuntimeError("internal plugin issue")
    host._plugins_changed = bad_refresh
    assert run(host) is False
    assert [event[0] for event in calls] == [
        "activate", "attach", "refresh", "diagnostic", "perf",
    ]
    assert len(queued) == 1
    queued[0]()
    assert calls[-1] == ("connect",)


def test_connect_callback_on_closed_window_does_not_touch_status_or_submit():
    connect, queued = _method("connect_configured_sources")
    host = NS(_closed=True)
    assert connect(host) is False
    assert queued == []
