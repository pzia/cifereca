"""P0 #1132 — GTK startup cannot resume after its owner is closed."""
from __future__ import annotations

import ast
import logging
import time
from pathlib import Path
from types import SimpleNamespace as NS

from cifereca.gui.startup_lifecycle import once_while_open


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
        "once_while_open": once_while_open,
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
    connect_if_open, _posted = _method("_connect_sources_if_open")
    host._connect_sources_if_open = lambda: connect_if_open(host)
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


def test_deferred_provider_connect_refuses_connection_after_close():
    run, queued = _method("activate_startup_plugins")
    host, calls = _host()
    assert run(host) is False
    assert len(queued) == 1
    host._closed = True
    assert queued[0]() is False
    assert ("connect",) not in calls


def test_connect_callback_on_closed_window_does_not_touch_status_or_submit():
    connect, queued = _method("connect_configured_sources")
    host = NS(_closed=True)
    assert connect(host) is False
    assert queued == []


def _pending_connection():
    connect, posted = _method("connect_configured_sources")
    calls = []
    queued = []
    host = NS(
        _closed=False,
        startup_status=NS(set_label=lambda text: calls.append(("startup", text))),
        dashboard_loading_status=NS(
            set_label=lambda text: calls.append(("dashboard-label", text)),
            set_visible=lambda value: calls.append(("dashboard-visible", value)),
        ),
        runner=NS(submit=lambda *args, **kwargs: queued.append((args, kwargs))),
        service=NS(last_loans=["loan"], connect_configured=lambda: None),
        _populate_accounts=lambda: calls.append(("accounts",)),
        _populate_loans=lambda rows: calls.append(("loans", rows)),
        _activity_result=lambda *args, **kwargs: calls.append(("activity",)),
        _render_dashboard=lambda: calls.append(("render",)),
        run_opening_sync=lambda: calls.append(("opening-sync",)),
    )
    assert connect(host) is False
    assert len(queued) == 1
    return host, calls, queued[0][0], posted


def test_queued_provider_success_does_not_mutate_closed_gtk_window():
    host, calls, submitted, posted = _pending_connection()
    calls.clear()
    host._closed = True
    submitted[2]({"connected": ["local"], "errors": {}})
    assert calls == []
    assert posted == []


def test_queued_provider_error_does_not_touch_closed_widgets():
    host, calls, submitted, posted = _pending_connection()
    calls.clear()
    host._closed = True
    submitted[3](RuntimeError("https://private.example/?token=SECRET"))
    assert calls == []
    assert posted == []


def test_live_provider_failure_is_functional_and_never_exposes_exception():
    host, calls, submitted, posted = _pending_connection()
    calls.clear()
    submitted[3](RuntimeError(
        "https://private.example/?token=SECRET /home/alice/private-card"
    ))
    assert calls == [
        ("startup", "Connexion aux sources indisponible."),
        ("dashboard-label",
         "Connexion aux sources indisponible. La navigation locale reste disponible."),
        ("dashboard-visible", True),
    ]
    assert "SECRET" not in str(calls)
    assert "private.example" not in str(calls)
    assert posted == []


def test_live_provider_success_preserves_dashboard_and_single_deferred_sync():
    host, calls, submitted, posted = _pending_connection()
    calls.clear()
    submitted[2]({"connected": ["provider"], "errors": {}})
    assert [row[0] for row in calls] == [
        "dashboard-visible", "accounts", "loans", "activity", "render",
    ]
    assert len(posted) == 1
    assert "opening-sync" not in [row[0] for row in calls]
    posted[0]()
    assert calls[-1] == ("opening-sync",)
