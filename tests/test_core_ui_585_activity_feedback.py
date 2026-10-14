from __future__ import annotations

import ast
from pathlib import Path

from cifereca.gui.activity_feedback import ActivityFeedback


ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "src/cifereca/gui/main_window.py"
RUNNER = ROOT / "src/cifereca/gui/task_runner.py"
SYNC = ROOT / "src/cifereca/gui/sync_controller.py"


def _method(path: Path, class_name: str, name: str) -> str:
    if name in {"run_opening_sync", "run_kobo_backup", "_build_sync_page"}:
        path = SYNC
        class_name = None
    tree = ast.parse(path.read_text(encoding="utf-8"))
    scope = (tree.body if class_name is None else next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name).body)
    method = next(node for node in scope if isinstance(node, ast.FunctionDef) and node.name == name)
    return ast.unparse(method)


def test_task_history_updates_one_row_per_operation():
    feedback = ActivityFeedback(limit=5)
    feedback.started(1, "Synchronisation…", "sync")
    feedback.finished(1, "Synchronisation…", "sync", success=True)
    feedback.result(1, "Synchronisation : 2 acquisitions", success=True)
    rows = feedback.rows()
    assert len(rows) == 1
    assert rows[0].display_text == "Terminé · Synchronisation : 2 acquisitions"


def test_success_then_error_stays_pending_until_acknowledged():
    feedback = ActivityFeedback()
    feedback.started(1, "Première", "one")
    feedback.finished(1, "Première", "one", success=True)
    feedback.started(2, "Seconde", "two")
    feedback.finished(2, "Seconde", "two", success=False, error="Réseau indisponible")
    assert feedback.latest_unacknowledged_error().task_id == 2
    assert feedback.latest_unacknowledged_error().display_text == "Erreur · Réseau indisponible"
    feedback.acknowledge_errors()
    assert feedback.latest_unacknowledged_error() is None


def test_history_is_bounded_and_newest_first():
    feedback = ActivityFeedback(limit=2)
    for task_id in (1, 2, 3):
        feedback.started(task_id, f"Tâche {task_id}", "task")
        feedback.finished(task_id, f"Tâche {task_id}", "task", success=True)
    assert [row.task_id for row in feedback.rows()] == [3, 2]


def test_main_footer_has_one_activity_row_with_history_and_acknowledgement():
    source = _method(MAIN, "MainWindow", "_build_main_shell")
    assert source.count("self.busy_revealer = Gtk.Revealer()") == 1
    assert "Gtk.MenuButton(label='Historique')" in source
    assert "self.activity_history_box" in source
    assert "Acquitter le résultat courant" in source


def test_task_feedback_names_running_success_and_error_states():
    started = _method(MAIN, "MainWindow", "_task_started")
    progress = _method(MAIN, "MainWindow", "_task_progress")
    finished = _method(MAIN, "MainWindow", "_task_finished")
    pending = _method(MAIN, "MainWindow", "_show_pending_activity_error")
    assert "En cours · " in started
    assert "En cours · " in progress
    assert "Terminé · " in finished
    assert "latest_unacknowledged_error" in pending


def test_success_reduces_automatically_but_error_has_no_timeout():
    finished = _method(MAIN, "MainWindow", "_task_finished")
    pending = _method(MAIN, "MainWindow", "_show_pending_activity_error")
    hidden = _method(MAIN, "MainWindow", "_hide_activity_feedback")
    assert "self._schedule_activity_reduce()" in finished
    assert "timeout_add_seconds" not in pending
    assert "_show_pending_activity_error()" in hidden


def test_later_success_cannot_mask_unacknowledged_error():
    source = _method(MAIN, "MainWindow", "_activity_result")
    assert "if self._show_pending_activity_error():" in source
    assert source.index("if self._show_pending_activity_error():") < source.index(
        "self.busy_label.set_label(f'Terminé · {message}')"
    )


def test_runner_success_toast_uses_activity_result_with_legacy_fallback():
    source = _method(RUNNER, "TaskRunner", "submit")
    assert "getattr(self.window, '_activity_result', None)" in source
    assert "reporter(success_toast, success=True, task_id=task_id)" in source
    assert "self.window.toast(success_toast)" in source


def test_sync_and_kobo_results_do_not_create_second_notification_row():
    opening = _method(MAIN, "MainWindow", "run_opening_sync")
    kobo = _method(MAIN, "MainWindow", "run_kobo_backup")
    sync_page = _method(MAIN, "MainWindow", "_build_sync_page")
    assert "self._activity_result(" in opening
    assert 'self.toast(f"Synchronisation :' not in opening
    assert "self._activity_result(" in kobo
    assert "self.toast(" not in kobo
    assert "Synchronisation terminée" not in sync_page


def test_common_task_success_callbacks_use_activity_bar():
    for name in (
        "connect_configured_sources",
        "_activate_identity",
        "_refresh_tools_page",
        "choose_acsm",
    ):
        source = _method(MAIN, "MainWindow", name)
        assert "self._activity_result(" in source


def test_task_callbacks_ignore_late_start_and_repeated_finish():
    started = _method(MAIN, "MainWindow", "_task_started")
    finished = _method(MAIN, "MainWindow", "_task_finished")
    assert "self._finished_task_ids.discard" not in started
    assert "register_task_start(" in started
    assert "register_task_finish(" in finished
    assert finished.index("register_task_finish(") < finished.index("self.activity_feedback.finished(")


def test_taskrunner_delivery_stops_when_success_callback_closes_window():
    """#1132: do not report a success toast after callback-triggered teardown."""
    import threading
    from types import SimpleNamespace

    tree = ast.parse(RUNNER.read_text(encoding="utf-8"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "TaskRunner")
    submit = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "submit")
    done = next(n for n in ast.walk(submit) if isinstance(n, ast.FunctionDef) and n.name == "done")
    deliver = next(n for n in ast.walk(done) if isinstance(n, ast.FunctionDef) and n.name == "deliver")
    namespace = {}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[deliver], type_ignores=[])), "<deliver>", "exec"), namespace)

    events = []
    window = SimpleNamespace(_closed=False)
    window._task_finished = lambda *args: events.append("finished")
    window._activity_result = lambda *args, **kwargs: events.append("toast")
    runner = SimpleNamespace(window=window, _lock=threading.Lock(), _count=1)
    def close_in_callback(value):
        events.append("ok")
        window._closed = True

    namespace.update(
        self=runner, control=None, outcome=("ok", "value"), task_id=1,
        label="test", ok=close_in_callback, err=None, success_toast="success",
    )
    assert namespace["deliver"]() is False
    assert events == ["finished", "ok"]
    assert runner._count == 0


def test_taskrunner_error_delivery_skips_callbacks_after_teardown():
    """#1132: closed windows must not receive late failure callbacks."""
    import threading
    from types import SimpleNamespace

    tree = ast.parse(RUNNER.read_text(encoding="utf-8"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "TaskRunner")
    submit = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "submit")
    done = next(n for n in ast.walk(submit) if isinstance(n, ast.FunctionDef) and n.name == "done")
    deliver = next(n for n in ast.walk(done) if isinstance(n, ast.FunctionDef) and n.name == "deliver")
    namespace = {}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[deliver], type_ignores=[])), "<deliver>", "exec"), namespace)

    events = []
    window = SimpleNamespace(_closed=True)
    window._task_finished = lambda *args: events.append("finished")
    runner = SimpleNamespace(window=window, _lock=threading.Lock(), _count=1)
    namespace.update(
        self=runner, control=None, outcome=("error", RuntimeError("failure")),
        task_id=1, label="test", ok=None,
        err=lambda error: events.append("error"), success_toast=None,
    )
    assert namespace["deliver"]() is False
    assert events == []
    assert runner._count == 0


def test_rejected_task_skips_error_callback_when_feedback_closes_window():
    """#1132: shutdown race must not invoke plugin error handlers after teardown."""
    import threading
    from types import SimpleNamespace

    tree = ast.parse(RUNNER.read_text(encoding="utf-8"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "TaskRunner")
    submit = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "submit")
    rejected = next(n for n in ast.walk(submit) if isinstance(n, ast.FunctionDef) and n.name == "rejected")
    namespace = {}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[rejected], type_ignores=[])), "<rejected>", "exec"), namespace)

    events = []
    window = SimpleNamespace(_closed=False)
    def finish(*args):
        events.append("finished")
        window._closed = True
    window._task_finished = finish
    runner = SimpleNamespace(window=window)
    namespace.update(
        self=runner, control=None, task_id=7, remaining=0, label="test",
        err=lambda error: events.append("error"),
    )
    assert namespace["rejected"]() is False
    assert events == ["finished"]


def test_finished_callback_keeps_running_state_with_stale_remaining_count():
    """#1132: GTK feedback must reflect live tasks, not queued runner counts."""
    import types

    tree = ast.parse(MAIN.read_text(encoding="utf-8"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "MainWindow")
    method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "_task_finished")
    from cifereca.gui.task_state import register_task_finish

    namespace = {"register_task_finish": register_task_finish}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])), "<finish>", "exec"), namespace)

    class Widget:
        def __init__(self): self.label = None
        def set_visible(self, value): pass
        def set_label(self, value): self.label = value
        def start(self): pass

    calls = []
    window = types.SimpleNamespace(
        _closed=False, _finished_task_ids=set(),
        _active_tasks={1: (0, "première", "task"), 2: (1, "seconde", "task")},
        _last_finished_task_id=None,
        _functional_error_message=lambda value: value,
        activity_feedback=types.SimpleNamespace(finished=lambda *args, **kwargs: calls.append(args[0])),
        _refresh_activity_history=lambda: None,
        busy_revealer=Widget(), busy_label=Widget(),
        activity_dismiss=Widget(), busy_spinner=Widget(),
    )
    finished = namespace["_task_finished"]
    assert finished(window, 1, 0, "première", True, None) is False
    assert window.busy_label.label == "En cours · seconde"
    assert set(window._active_tasks) == {2}
    assert finished(window, 1, 0, "première", True, None) is False
    assert calls == [1]


def test_finished_callback_counts_two_live_tasks_when_runner_reports_one():
    """#1132: multiple still-running tasks must be counted from GTK state."""
    import types

    tree = ast.parse(MAIN.read_text(encoding="utf-8"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "MainWindow")
    method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "_task_finished")
    from cifereca.gui.task_state import register_task_finish

    namespace = {"register_task_finish": register_task_finish}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])), "<finish>", "exec"), namespace)

    class Widget:
        def __init__(self): self.label = None
        def set_visible(self, value): pass
        def set_label(self, value): self.label = value
        def start(self): pass

    window = types.SimpleNamespace(
        _closed=False, _finished_task_ids=set(),
        _active_tasks={
            1: (0, "terminée", "task"),
            2: (1, "en cours", "task"),
            3: (2, "en attente", "task"),
        },
        _last_finished_task_id=None,
        _functional_error_message=lambda value: value,
        activity_feedback=types.SimpleNamespace(finished=lambda *args, **kwargs: None),
        _refresh_activity_history=lambda: None,
        busy_revealer=Widget(), busy_label=Widget(),
        activity_dismiss=Widget(), busy_spinner=Widget(),
    )
    assert namespace["_task_finished"](window, 1, 1, "terminée", True, None) is False
    assert window.busy_label.label == "En cours · 2 opérations · en cours"


def test_duplicate_task_start_does_not_duplicate_activity_feedback():
    """#1132: a repeated GTK start delivery is idempotent."""
    import types

    tree = ast.parse(MAIN.read_text(encoding="utf-8"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "MainWindow")
    method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "_task_started")
    from cifereca.gui.task_state import register_task_start

    namespace = {"register_task_start": register_task_start}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])), "<start>", "exec"), namespace)

    calls = []
    window = types.SimpleNamespace(
        _closed=False, _finished_task_ids=set(),
        _active_tasks={7: (1, "existante", "task")},
        _cancel_activity_hide=lambda: calls.append("cancel"),
        activity_feedback=types.SimpleNamespace(started=lambda *args: calls.append("started")),
        _refresh_activity_history=lambda: calls.append("refresh"),
    )
    assert namespace["_task_started"](window, 7, 1, "existante", 1, "task") is False
    assert calls == []
    assert window._active_tasks == {7: (1, "existante", "task")}


def test_stale_progress_cannot_replace_live_task_banner():
    """#1132: a delayed progress update must not show a finished operation."""
    import types

    method = _method(MAIN, "MainWindow", "_task_progress")
    namespace = {}
    exec(method, namespace)
    labels = []
    window = types.SimpleNamespace(
        _closed=False,
        _active_tasks={2: (1, "Synchronisation", "sync")},
        busy_revealer=object(),
        busy_label=types.SimpleNamespace(
            set_visible=lambda *_: None,
            set_label=labels.append,
        ),
    )
    assert namespace["_task_progress"](window, "Téléchargement terminé") is False
    assert labels == ["En cours · Synchronisation"]
    assert namespace["_task_progress"](window, "Synchronisation 50 %") is False
    assert labels[-1] == "En cours · Synchronisation 50 %"
    window._active_tasks.clear()
    assert namespace["_task_progress"](window, "Téléchargement terminé") is False
    assert len(labels) == 2
