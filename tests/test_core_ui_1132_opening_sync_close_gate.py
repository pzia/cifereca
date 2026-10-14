"""P0 #1132: deferred opening sync never starts after GTK owner teardown."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS


SOURCE = Path(__file__).resolve().parents[1] / "src/cifereca/gui/sync_controller.py"


def _method():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    fn = next(node for node in tree.body
              if isinstance(node, ast.FunctionDef) and node.name == "run_opening_sync")
    queued = []
    ns = {
        "TaskPriority": NS(USER_ACTION=1, MAINTENANCE=2, ACTIVE_LOAN=3),
        "GLib": NS(idle_add=lambda callback, *args: queued.append((callback, args))),
    }
    module = ast.fix_missing_locations(ast.Module(
        body=[ast.ImportFrom(module="__future__",
                             names=[ast.alias(name="annotations")], level=0),
              fn], type_ignores=[],
    ))
    exec(compile(module, str(SOURCE), "exec"), ns)
    return ns["run_opening_sync"], queued


def _host():
    tasks = []
    effects = []
    service = NS(
        last_loans=["existing"],
        opening_sync=lambda **kwargs: effects.append(("service-work", kwargs)),
    )
    host = NS(
        _closed=False,
        runner=NS(submit=lambda *args, **kwargs: tasks.append((args, kwargs))),
        service=service,
        diagnostics=NS(add=lambda *args, **kwargs: effects.append(("diagnostics",))),
        _render_dashboard=lambda: effects.append(("dashboard",)),
        _populate_loans=lambda rows: effects.append(("loans", rows)),
        refresh_library=lambda force: effects.append(("library", force)),
        refresh_recent=lambda: effects.append(("recent",)),
        _activity_result=lambda *args, **kwargs: effects.append(("activity",)),
    )
    return host, tasks, effects


def test_post_close_glib_opening_sync_callback_does_not_enqueue_service_work():
    start, scheduled = _method()
    # Only _closed is available: accessing an already disposed service or
    # runner would raise, including a second implicit sync after close.
    assert start(NS(_closed=True)) is None
    assert scheduled == []


def test_open_window_still_schedules_once_with_original_priority_and_kind():
    start, scheduled = _method()
    host, tasks, effects = _host()
    assert start(host) is None
    assert len(tasks) == 1
    args, kwargs = tasks[0]
    assert args[0] == "Synchronisation des bibliothèques…"
    assert kwargs["priority"] == 3
    assert kwargs["kind"] == "opening-sync"
    assert kwargs["control"] is None
    assert kwargs["with_progress"] is True
    assert scheduled == []
    args[1](lambda label: scheduled.append(label))
    assert [item[0] for item in effects] == ["service-work"]


def test_closed_owner_drops_progress_completion_and_error_without_gtk_access():
    start, scheduled = _method()
    host, tasks, effects = _host()
    start(host)
    args, _kwargs = tasks[0]
    captured = []
    host.service.opening_sync = lambda **kwargs: captured.append(kwargs["progress"])
    args[1](lambda label: scheduled.append(label))
    progress_fn = captured[0]
    host._closed = True
    progress_fn({"event": "identity_begin", "identity_id": "camille"})
    args[2]({"identities": [{"new_acquisitions": 1}]})
    args[3](RuntimeError("private provider details"))
    assert scheduled == []
    assert effects == []


def test_open_owner_retains_progress_and_success_rendering():
    start, scheduled = _method()
    host, tasks, effects = _host()
    start(host, deep=True)
    args, kwargs = tasks[0]
    assert args[0] == "Vérification complète de la bibliothèque…"
    assert kwargs["kind"] == "deep-sync"
    assert kwargs["priority"] == 2
    captured = []
    host.service.opening_sync = lambda **kwargs: captured.append(kwargs["progress"])
    args[1](lambda label: scheduled.append(label))
    captured[0]({"event": "identity_begin", "label": "Camille"})
    assert scheduled == ["Synchronisation · Camille…"]
    args[2]({"identities": [{"new_acquisitions": 1}]})
    assert [row[0] for row in effects] == [
        "dashboard", "loans", "library", "recent", "activity",
    ]
