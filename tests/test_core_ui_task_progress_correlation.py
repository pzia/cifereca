"""Real TaskRunner + opening-sync producer + UI adapter, without GI.

Only GLib scheduling and widgets are doubled. Workers use the real priority
executor; tests dispatch queued UI callbacks on the owning thread.
"""
from __future__ import annotations

import ast
import itertools
import logging
import threading
import time
from pathlib import Path
from queue import Queue
from types import MethodType, SimpleNamespace as NS

import pytest

from cifereca.gui import task_activity_adapter
from cifereca.gui.activity_feedback import ActivityFeedback
from cifereca.gui.task_progress_presentation import live_task_progress_label
from cifereca.task_queue import PriorityExecutor, TaskPriority


GUI = Path(__file__).resolve().parents[1] / "src/cifereca/gui"


def load_declaration(filename, name, namespace):
    path = GUI / filename
    owner = ast.parse(path.read_text())
    node = next(n for n in owner.body if getattr(n, "name", None) == name)
    module = ast.fix_missing_locations(ast.Module(body=[
        ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
        node,
    ], type_ignores=[]))
    exec(compile(module, str(path), "exec"), namespace)
    return namespace[name]


@pytest.fixture
def host():
    queue = Queue()
    ui_thread = threading.get_ident()
    glib = NS(idle_add=lambda fn, *args: queue.put((fn, args)))
    runner_type = load_declaration("task_runner.py", "TaskRunner", {
        "itertools": itertools, "threading": threading, "time": time,
        "_TRACE_LOG": logging.getLogger(__name__), "GLib": glib,
        "PriorityExecutor": PriorityExecutor, "TaskPriority": TaskPriority,
    })
    start = load_declaration("sync_controller.py", "run_opening_sync", {
        "TaskPriority": TaskPriority, "GLib": glib,
    })

    class Widget:
        text = ""

        def __getattr__(self, name):
            def call(*args):
                assert threading.get_ident() == ui_thread
                if name == "set_label":
                    self.text = args[0]
            return call

    window = NS(
        _closed=False, _active_tasks={}, _finished_task_ids=set(),
        activity_feedback=ActivityFeedback(), _cancel_activity_hide=lambda: None,
        _refresh_activity_history=lambda: None, _schedule_activity_reduce=lambda: None,
        _show_pending_activity_error=lambda: False,
        _functional_error_message=lambda error: error,
        _render_dashboard=lambda: None, _populate_loans=lambda rows: None,
        refresh_library=lambda force: None, refresh_recent=lambda: None,
        _activity_result=lambda *args, **kwargs: None,
    )
    for name in ("busy_revealer", "busy_label", "activity_status_icon",
                 "activity_dismiss", "busy_spinner"):
        setattr(window, name, Widget())
    window._task_started = lambda *args: task_activity_adapter.task_started(window, *args)
    window._task_finished = lambda *args: task_activity_adapter.task_finished(window, *args)
    main = ast.parse((GUI / "main_window.py").read_text())
    cls = next(n for n in main.body if isinstance(n, ast.ClassDef) and n.name == "MainWindow")
    method = next(n for n in cls.body if getattr(n, "name", None) == "_task_progress_for_task")
    namespace = {"task_activity_adapter": task_activity_adapter}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])),
                 "main_window.py", "exec"), namespace)
    window._task_progress_for_task = MethodType(namespace[method.name], window)
    window.runner = runner_type(window, max_workers=2)
    calls = []
    gates = []
    callbacks = []

    def opening_sync(progress, *, deep):
        assert threading.get_ident() != ui_thread
        gate = threading.Event()
        gates.append(gate)
        callbacks.append(progress)
        calls.append(deep)
        progress({"event": "identity_begin", "label": "Camille"})
        assert gate.wait(5), "test worker was not released"
        return {"identities": []}

    window.service = NS(opening_sync=opening_sync, last_loans=[])

    def dispatch_until(predicate):
        deadline = time.monotonic() + 3
        while not predicate():
            callback, args = queue.get(timeout=max(0.01, deadline - time.monotonic()))
            callback(*args)
            assert time.monotonic() < deadline

    def dispatch_remaining():
        while not queue.empty():
            callback, args = queue.get_nowait()
            callback(*args)

    yield NS(window=window, start=start, calls=calls, callbacks=callbacks,
             gates=gates, dispatch_until=dispatch_until,
             dispatch_remaining=dispatch_remaining, queue=queue)
    window._closed = True
    for gate in gates:
        gate.set()
    window.runner.close()


@pytest.mark.parametrize("deep", [False, True])
def test_real_opening_sync_keeps_identity_progress_in_both_modes(host, deep):
    w = host.window
    host.start(w, deep=deep)
    host.dispatch_until(lambda: w.busy_label.text == "En cours · Synchronisation · Camille…")
    assert host.calls == [deep]
    task_id = next(iter(w._active_tasks))
    priority, title, kind = w._active_tasks[task_id]
    assert kind == ("deep-sync" if deep else "opening-sync")
    assert priority == int(TaskPriority.MAINTENANCE if deep else TaskPriority.ACTIVE_LOAN)
    assert title == ("Vérification complète de la bibliothèque…" if deep
                     else "Synchronisation des bibliothèques…")
    host.gates[0].set()
    host.dispatch_until(lambda: task_id in w._finished_task_ids)
    finished_label = w.busy_label.text
    host.callbacks[0]({"event": "identity_begin", "label": "Ancien profil"})
    host.dispatch_remaining()
    assert w.busy_label.text == finished_label
    assert len(w.activity_feedback.rows()) == 1


def test_retained_callback_cannot_overwrite_another_task_with_the_same_title(host):
    w = host.window
    host.start(w)
    host.dispatch_until(lambda: w.busy_label.text.endswith("Synchronisation · Camille…"))
    first_id = next(iter(w._active_tasks))
    host.start(w)
    host.dispatch_until(lambda: len(host.callbacks) == 2 and w.busy_label.text == (
        "En cours · 2 opérations · Synchronisation · Camille…"
    ))
    host.gates[0].set()
    host.dispatch_until(lambda: first_id in w._finished_task_ids)
    host.callbacks[1]({"event": "identity_begin", "label": "Alex"})
    host.dispatch_remaining()
    assert w.busy_label.text == "En cours · Synchronisation · Alex…"
    host.callbacks[0]({"event": "identity_begin", "label": "Camille périmée"})
    host.dispatch_remaining()
    assert w.busy_label.text == "En cours · Synchronisation · Alex…"
    assert len(w._active_tasks) == 1


def test_queued_progress_after_completion_is_inert_while_a_sibling_is_live(host):
    w = host.window
    host.start(w)
    host.dispatch_until(lambda: bool(host.callbacks) and bool(w._active_tasks))
    task_id = next(iter(w._active_tasks))
    # Simulate an idle source delayed beyond the task's completion delivery.
    task_activity_adapter.task_finished(w, task_id, 0, "Synchronisation", True, None)
    task_activity_adapter.task_started(w, task_id + 1, 1, "Lecture", 1, "read")
    host.dispatch_remaining()
    assert w.busy_label.text == "En cours · Lecture"


def test_closure_drops_both_queued_and_new_worker_progress(host):
    w = host.window
    host.start(w)
    host.dispatch_until(lambda: bool(host.callbacks) and bool(w._active_tasks))
    w._closed = True
    before = w.busy_label.text
    host.callbacks[0]({"event": "identity_begin", "label": "Après fermeture"})
    host.dispatch_remaining()
    assert w.busy_label.text == before


def test_explicit_identity_is_independent_of_wording_and_unknown_ids_are_rejected():
    tasks = {1: (1, "Titre identique", "sync"), 2: (1, "Titre identique", "sync")}
    assert live_task_progress_label(tasks, "Camille…", task_id=2) == (
        "En cours · 2 opérations · Camille…"
    )
    assert live_task_progress_label(tasks, "Titre identique 50 %", task_id=3) is None
