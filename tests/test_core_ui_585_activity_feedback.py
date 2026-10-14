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
    assert "if int(task_id) in self._finished_task_ids:" in started
    assert "if task_id in self._finished_task_ids:" in finished
    assert finished.index("if task_id in self._finished_task_ids:") < finished.index("self.activity_feedback.finished(")


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
