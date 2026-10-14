"""P0 #1132: enqueue/close race cannot strand GTK feedback."""
from __future__ import annotations

import ast
import itertools
import logging
import threading
import time
from pathlib import Path
from types import SimpleNamespace as NS


SOURCE = Path(__file__).resolve().parents[1] / "src/cifereca/gui/task_runner.py"


def _submit():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    owner = next(node for node in tree.body
                 if isinstance(node, ast.ClassDef) and node.name == "TaskRunner")
    method = next(node for node in owner.body
                  if isinstance(node, ast.FunctionDef) and node.name == "submit")
    future = ast.ImportFrom(
        module="__future__", names=[ast.alias(name="annotations")], level=0,
    )
    module = ast.fix_missing_locations(ast.Module(
        body=[future, method], type_ignores=[],
    ))
    idle = []
    namespace = {
        "GLib": NS(idle_add=lambda fn, *args: idle.append((fn, args))),
        "TaskPriority": NS(NORMAL=40),
        "_TRACE_LOG": logging.getLogger("task-runner-close-race-tests"),
        "time": time,
    }
    exec(compile(module, str(SOURCE), "exec"), namespace)
    return namespace["submit"], idle


class Button:
    def __init__(self):
        self.calls = []

    def set_sensitive(self, value):
        self.calls.append(value)

    def get_root(self):
        return object()


def _runner(on_submit=None):
    events = []
    closed = NS(value=False)
    window = NS(
        _closed=False,
        _task_started=lambda *args: events.append(("started", args)),
        _task_finished=lambda *args: events.append(("finished", args)),
    )

    class RejectingExecutor:
        def submit(self, *args):
            if on_submit is not None:
                on_submit(window)
            raise RuntimeError("private /home/borrower/token")

    runner = NS(
        window=window, pool=RejectingExecutor(), _lock=threading.Lock(),
        _count=0, _task_ids=itertools.count(1),
        present_error=lambda exception: events.append(("error", str(exception))),
    )
    return runner, window, events


def test_rejected_enqueue_restores_button_and_activity_count():
    submit, pending = _submit()
    runner, window, events = _runner()
    button = Button()
    errors = []
    submit(runner, "Un prêt", lambda: None, err=errors.append, control=button)
    assert runner._count == 0
    assert button.calls == [False]
    assert len(pending) == 2
    for index, (callback, args) in enumerate(pending):
        assert callback(*args) is (None if index == 0 else False)
    assert button.calls == [False, True]
    assert [event[0] for event in events] == ["started", "finished"]
    assert events[-1][1][3] is False
    assert events[-1][1][4] == "Traitement indisponible"
    assert len(errors) == 1
    assert str(errors[0]) == "Traitement indisponible"
    assert "borrower" not in str(errors)


def test_enqueue_closes_concurrently_without_late_widget_access():
    submit, pending = _submit()
    runner, window, events = _runner(on_submit=lambda w: setattr(w, "_closed", True))
    button = Button()
    submit(runner, "Connexion", lambda: None, control=button)
    assert runner._count == 0
    assert button.calls == [False]
    assert len(pending) == 2
    assert pending[-1][0](*pending[-1][1]) is False
    assert button.calls == [False]
    assert events == []


def test_rejection_without_custom_error_callback_uses_safe_generic_message():
    submit, pending = _submit()
    runner, window, events = _runner()
    submit(runner, "Rafraîchir", lambda: None)
    for callback, args in pending:
        callback(*args)
    assert events[-1] == ("error", "Traitement indisponible")
    assert runner._count == 0


def test_detached_control_is_never_touched_at_submit_or_rejection():
    """#1132: late plugin action may hold a button removed by navigation."""
    submit, pending = _submit()
    runner, window, events = _runner()
    class DetachedButton(Button):
        def get_root(self):
            return None
        def set_sensitive(self, _value):
            raise AssertionError("detached GTK control accessed")

    control = DetachedButton()
    failures = []
    submit(runner, "Ancienne page", lambda: None, err=failures.append, control=control)
    assert runner._count == 0
    assert len(pending) == 2
    for callback, args in pending:
        callback(*args)
    assert [event[0] for event in events] == ["started", "finished"]
    assert len(failures) == 1
    assert str(failures[0]) == "Traitement indisponible"


def test_success_callback_closing_window_does_not_emit_late_toast():
    """#1132: a success callback can synchronously tear down MainWindow."""
    submit, pending = _submit()
    runner, window, events = _runner()
    class Finished:
        def __init__(self, value):
            self.value = value
        def result(self):
            return self.value
        def add_done_callback(self, callback):
            callback(self)
    class ImmediateExecutor:
        def submit(self, _priority, fn):
            return Finished(fn())
    runner.pool = ImmediateExecutor()
    window._activity_result = lambda *args, **kwargs: events.append(("toast", args))
    submit(
        runner, "Connexion", lambda: "ok",
        ok=lambda _value: setattr(window, "_closed", True),
        success_toast="Succès",
    )
    assert len(pending) == 2
    for callback, args in pending:
        callback(*args)
    assert runner._count == 0
    assert [event[0] for event in events] == ["started", "finished"]
    assert window._closed is True


def test_success_callback_with_live_window_preserves_single_toast():
    """#1132: keep normal success notification when the owner is alive."""
    submit, pending = _submit()
    runner, window, events = _runner()
    class Finished:
        def result(self):
            return "ok"
        def add_done_callback(self, callback):
            callback(self)
    runner.pool = NS(submit=lambda _priority, _fn: Finished())
    window._activity_result = lambda *args, **kwargs: events.append(("toast", args))
    submit(runner, "Connexion", lambda: "ok", success_toast="Succès")
    for callback, args in pending:
        callback(*args)
    assert runner._count == 0
    assert [event[0] for event in events] == ["started", "finished", "toast"]
    assert events[-1][1] == ("Succès",)
