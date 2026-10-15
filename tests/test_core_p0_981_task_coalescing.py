"""P0 #981: deterministic real TaskRunner concurrency without importing GTK.

The public TaskRunner declaration and real PriorityExecutor are exercised via
the same AST/GLib seam as the existing CoreUI regression suite. No timers,
network, provider I/O, or MainWindow implementation is loaded.
"""
from __future__ import annotations

import ast
import itertools
import logging
from pathlib import Path
from queue import Queue
import threading
import time
from types import SimpleNamespace as NS

import pytest

from cifereca.task_queue import PriorityExecutor, TaskPriority


ROOT = Path(__file__).resolve().parents[1] / "src/cifereca/gui"


class Control:
    def __init__(self):
        self.enabled = []

    def get_root(self):
        return object()

    def set_sensitive(self, enabled):
        self.enabled.append(enabled)


@pytest.fixture
def session():
    queue = Queue()
    events = []
    log = logging.getLogger("test-core-981-task")
    window = NS(
        _closed=False,
        _task_started=lambda *args: events.append(("started", args)),
        _task_finished=lambda *args: events.append(("finished", args)),
        _task_progress_for_task=lambda *args: events.append(("progress", args)),
        show_error=lambda *args: events.append(("error", args)),
    )
    node = next(n for n in ast.parse((ROOT / "task_runner.py").read_text()).body
                if isinstance(n, ast.ClassDef) and n.name == "TaskRunner")
    declaration = ast.fix_missing_locations(ast.Module(
        body=[ast.ImportFrom(module="__future__",
                             names=[ast.alias(name="annotations")], level=0),
              node], type_ignores=[],
    ))
    namespace = dict(
        threading=threading, itertools=itertools, time=time,
        GLib=NS(idle_add=lambda callback, *args: queue.put((callback, args))),
        _TRACE_LOG=log, TaskPriority=TaskPriority, PriorityExecutor=PriorityExecutor,
    )
    exec(compile(declaration, str(ROOT / "task_runner.py"), "exec"), namespace)
    runner = namespace["TaskRunner"](window, max_workers=4)
    gates = []

    def drain_until(predicate, *, timeout=5):
        deadline = time.monotonic() + timeout
        while not predicate():
            callback, args = queue.get(timeout=max(0.01, deadline - time.monotonic()))
            callback(*args)
            assert time.monotonic() < deadline, "UI callback deadline exceeded"

    def drain_now():
        while not queue.empty():
            callback, args = queue.get_nowait()
            callback(*args)

    def work(*, fail=False):
        gate = threading.Event()
        entered = threading.Event()
        gates.append(gate)
        calls = []
        def perform():
            calls.append("started")
            entered.set()
            assert gate.wait(5), "worker not released"
            if fail:
                raise RuntimeError("PRIVATE_BEARER_URL")
            return "result"
        return perform, entered, gate, calls

    yield NS(runner=runner, window=window, queue=queue, events=events,
             work=work, drain_until=drain_until, drain_now=drain_now)
    window._closed = True
    for gate in gates:
        gate.set()
    runner.close()
    drain_now()


def test_five_identical_auto_submissions_create_one_worker_one_activity(session):
    s = session
    worker, entered, release, calls = s.work()
    outcomes = []
    for _ in range(5):
        s.runner.submit(
            "Automatique", worker, ok=outcomes.append,
            kind="opening-sync", coalesce_key="global:shallow", origin="automatic",
        )
    assert entered.wait(3)
    s.drain_now()
    assert calls == ["started"]
    assert s.runner._count == 1
    assert len(s.runner._coalesced) == 1
    assert [kind for kind, _ in s.events] == ["started"]
    release.set()
    s.drain_until(lambda: len(outcomes) == 1)
    assert outcomes == ["result"]
    assert [kind for kind, _ in s.events] == ["started", "finished"]
    assert s.runner._count == 0
    assert s.runner._coalesced == {}


def test_distinct_task_scope_and_kind_are_never_coalesced(session):
    s = session
    tasks = [s.work() for _ in range(3)]
    for (worker, _, _, _), kind, key in zip(
        tasks, ("opening-sync", "opening-sync", "source-sync"),
        ("identity-a", "identity-b", "identity-a"),
    ):
        s.runner.submit("Même libellé", worker, kind=kind, coalesce_key=key)
    for _, entered, _, _ in tasks:
        assert entered.wait(3)
    assert s.runner._count == 3 and len(s.runner._coalesced) == 3
    for _, _, gate, _ in tasks:
        gate.set()
    s.drain_until(lambda: len([e for e in s.events if e[0] == "finished"]) == 3)
    assert not s.runner._coalesced and s.runner._count == 0


def test_manual_button_bypasses_even_identical_auto_key(session):
    s = session
    background, bg_entered, bg_gate, bg_calls = s.work()
    manual, manual_entered, manual_gate, manual_calls = s.work()
    button = Control()
    s.runner.submit("Ouverture", background, kind="opening-sync",
                    coalesce_key="global:shallow", origin="automatic")
    assert bg_entered.wait(3)
    s.runner.submit("Ouverture", manual, control=button, kind="opening-sync",
                    coalesce_key="global:shallow", origin="user")
    assert manual_entered.wait(3)
    assert bg_calls == manual_calls == ["started"]
    assert s.runner._count == 2 and len(s.runner._coalesced) == 1
    assert button.enabled == [False]
    bg_gate.set()
    manual_gate.set()
    s.drain_until(lambda: len([e for e in s.events if e[0] == "finished"]) == 2)
    assert button.enabled == [False, True]
    assert not s.runner._coalesced


def test_result_in_gtk_queue_is_reused_until_delivery(session):
    s = session
    worker, entered, gate, calls = s.work()
    s.runner.submit("Auto", worker, kind="opening-sync", coalesce_key="global:shallow")
    assert entered.wait(3)
    s.drain_until(lambda: len([e for e in s.events if e[0] == "started"]) == 1)
    gate.set()
    # Hold the completion function in the UI queue; it has not been delivered.
    deadline = time.monotonic() + 3
    while True:
        callback, args = s.queue.get(timeout=max(0.01, deadline - time.monotonic()))
        if callback.__name__ == "deliver":
            break
        callback(*args)
        assert time.monotonic() < deadline
    assert s.runner._count == 1
    for _ in range(4):
        s.runner.submit("Auto", worker, kind="opening-sync",
                        coalesce_key="global:shallow")
    assert calls == ["started"] and len(s.runner._coalesced) == 1
    callback(*args)
    assert s.runner._count == 0 and not s.runner._coalesced
    # The same work is allowed to run again once its prior result was shown.
    s.runner.submit("Auto", lambda: "fresh", kind="opening-sync",
                    coalesce_key="global:shallow")
    s.drain_until(lambda: len([e for e in s.events if e[0] == "finished"]) == 2)


def test_error_is_not_mislabelled_success_and_failed_scope_can_retry(session, caplog):
    s = session
    worker, entered, gate, calls = s.work(fail=True)
    failures = []
    with caplog.at_level(logging.INFO, logger="test-core-981-task"):
        s.runner.submit("Auto", worker, err=failures.append, kind="opening-sync",
                        coalesce_key="global:shallow", origin="automatic")
        assert entered.wait(3)
        s.runner.submit("Auto", worker, kind="opening-sync",
                        coalesce_key="global:shallow", origin="automatic")
        gate.set()
        s.drain_until(lambda: len(failures) == 1)
    assert isinstance(failures[0], RuntimeError)
    assert calls == ["started"]
    assert s.events[-1][0] == "finished"
    assert s.events[-1][1][3] is False
    assert "status=failed" in caplog.text
    assert "PRIVATE_BEARER_URL" not in caplog.text
    assert not s.runner._coalesced
    s.runner.submit("Réessai", lambda: "recovered", kind="opening-sync",
                    coalesce_key="global:shallow")
    s.drain_until(lambda: len([e for e in s.events if e[0] == "finished"]) == 2)
    assert s.events[-1][1][3] is True


def test_pool_shutdown_rejection_releases_reservation_and_counter(session):
    s = session
    def reject(_priority, _fn):
        raise RuntimeError("PRIVATE_CREDENTIAL")
    s.runner.pool.submit = reject
    errors = []
    s.runner.submit("Auto", lambda: None, err=errors.append,
                    kind="opening-sync", coalesce_key="global:shallow")
    assert not s.runner._coalesced and s.runner._count == 0
    s.drain_now()
    assert len(errors) == 1 and str(errors[0]) == "Traitement indisponible"
    assert s.events[1][0] == "finished"
    assert s.events[1][1][3] is False


def test_close_clears_scope_and_late_completion_does_not_touch_window(session):
    s = session
    worker, entered, gate, calls = s.work()
    s.runner.submit("Auto", worker, kind="opening-sync",
                    coalesce_key="global:shallow")
    assert entered.wait(3)
    assert s.runner._coalesced
    s.window._closed = True
    s.runner.close()
    assert not s.runner._coalesced
    gate.set()
    s.drain_now()
    assert not any(event[0] == "finished" for event in s.events)


@pytest.mark.parametrize("key", ["", "x" * 129, 4, [], ("private",)])
def test_invalid_keys_are_rejected_before_any_queue_or_worker(session, key):
    s = session
    with pytest.raises(ValueError, match="invalid task coalescing key"):
        s.runner.submit("Invalid", lambda: None, coalesce_key=key)
    assert not s.runner._coalesced and s.runner._count == 0
    assert s.queue.empty()


@pytest.mark.parametrize("origin", [
    "PRIVATE_TOKEN_FROM_PLUGIN", ["PRIVATE_TOKEN_FROM_PLUGIN"],
    {"PRIVATE_TOKEN_FROM_PLUGIN": 1}, None, 1,
])
def test_unknown_untrusted_origin_is_not_reflected_in_trace(session, caplog, origin):
    s = session
    with caplog.at_level(logging.INFO, logger="test-core-981-task"):
        s.runner.submit("Public", lambda: 1, origin=origin, kind="opening-sync")
        s.drain_until(lambda: len([e for e in s.events if e[0] == "finished"]) == 1)
    assert "origin=unspecified" in caplog.text
    assert "PRIVATE_TOKEN_FROM_PLUGIN" not in caplog.text


def test_legacy_submission_without_key_still_runs_every_request(session):
    s = session
    gate = threading.Event()
    started = []
    def worker():
        started.append(threading.get_ident())
        assert gate.wait(5)
    for _ in range(2):
        s.runner.submit("Legacy", worker, kind="opening-sync")
    deadline = time.monotonic() + 3
    while len(started) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert len(started) == 2
    assert s.runner._count == 2 and not s.runner._coalesced
    gate.set()
    s.drain_until(lambda: len([e for e in s.events if e[0] == "finished"]) == 2)



def test_destroyed_gtk_control_cannot_strand_task_reservation(session):
    """#981/#1132: control liveness check precedes the new coalescing slot."""
    s = session
    class DestroyedControl:
        def get_root(self):
            raise RuntimeError("Gtk widget was disposed")
    with pytest.raises(RuntimeError, match="disposed"):
        s.runner.submit(
            "Manual", lambda: pytest.fail("must not enqueue"),
            control=DestroyedControl(), kind="opening-sync",
            coalesce_key="global:shallow", origin="user",
        )
    assert s.runner._count == 0
    assert not s.runner._coalesced
    assert s.queue.empty()
