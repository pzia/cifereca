from __future__ import annotations

import ast
from pathlib import Path

from cifereca.gui.activity_feedback import ActivityFeedback


ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "src/cifereca/gui/main_window.py"
RUNNER = ROOT / "src/cifereca/gui/task_runner.py"


def _method(path: Path, class_name: str, name: str) -> str:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == name)
    return ast.unparse(method)


def test_task_history_updates_one_row_per_operation():
    feedback = ActivityFeedback(limit=5)
    feedback.started(1, "Synchronisation…", "sync")
    feedback.finished(1, "Synchronisation…", "sync", success=True)
    feedback.result(1, "Synchronisation : 2 acquisitions", success=True)
    rows = feedback.rows()
    assert len(rows) == 1
    assert rows[0].display_text == "Terminé · Synchronisation : 2 acquisitions"


def test_success_then_error_keeps_unacknowledged_error_until_acknowledged():
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


def test_main_footer_has_one_revealer_with_expandable_history_and_acknowledgement():
    source = _method(MAIN, "MainWindow", "_build_main_shell")
    assert source.count("self.busy_revealer = Gtk.Revealer()") == 1
    assert "Gtk.MenuButton(label='Historique')" in source
    assert "self.activity_history_box" in source
    assert "Acquitter le résultat courant" in source


def test_task_feedback_explicitly_labels_running_success_and_error():
    started = _method(MAIN, "MainWindow", "_task_started")
    progress = _method(MAIN, "MainWindow", "_task_progress")
    finished = _method(MAIN, "MainWindow", "_task_finished")
    assert "En cours · " in started
    assert "En cours · " in progress
    assert "Terminé · " in finished
    assert "_show_pending_activity_error()" in finished


def test_success_toast_is_consolidated_into_activity_result():
    source = _method(RUNNER, "TaskRunner", "submit")
    assert "self.window._activity_result(" in source
    assert "self.window.toast(success_toast)" not in source


def test_sync_summary_updates_task_result_instead_of_creating_second_toast():
    source = _method(MAIN, "MainWindow", "run_opening_sync")
    assert "self._activity_result(" in source
    assert 'self.toast(f"Synchronisation :' not in source


def test_success_auto_reduces_but_errors_are_not_timed_out():
    finished = _method(MAIN, "MainWindow", "_task_finished")
    pending = _method(MAIN, "MainWindow", "_show_pending_activity_error")
    hidden = _method(MAIN, "MainWindow", "_hide_activity_feedback")
    assert "self._schedule_activity_reduce()" in finished
    assert "timeout_add_seconds" not in pending
    assert "latest_unacknowledged_error" in hidden
