"""P0 #1132: actual task feedback adapter with headless window doubles."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS

from cifereca.gui.activity_feedback import ActivityFeedback
from cifereca.gui.task_activity_adapter import task_started, task_progress, task_finished


ROOT = Path(__file__).resolve().parents[1] / "src/cifereca/gui"
MAIN = ROOT / "main_window.py"
OWNER = ROOT / "task_activity_adapter.py"


class Widget:
    def __init__(self):
        self.calls = []
        self.label = None

    def set_visible(self, visible):
        self.calls.append(("visible", visible))

    def set_label(self, value):
        self.label = value
        self.calls.append(("label", value))

    def start(self):
        self.calls.append(("start", None))

    def stop(self):
        self.calls.append(("stop", None))

    def set_from_icon_name(self, name):
        self.calls.append(("icon", name))

    def add_css_class(self, name):
        self.calls.append(("add", name))

    def remove_css_class(self, name):
        self.calls.append(("remove", name))

    def set_reveal_child(self, reveal):
        self.calls.append(("reveal", reveal))


def make_window(*, with_widgets=True):
    actions = []
    window = NS(
        _closed=False,
        _active_tasks={},
        _finished_task_ids=set(),
        _last_finished_task_id=None,
        activity_feedback=ActivityFeedback(limit=20),
        _cancel_activity_hide=lambda: actions.append("cancel"),
        _refresh_activity_history=lambda: actions.append("history"),
        _functional_error_message=lambda message: message or "Échec",
        _schedule_activity_reduce=lambda: actions.append("reduce"),
    )
    window._show_pending_activity_error = lambda: (
        window.activity_feedback.latest_unacknowledged_error() is not None
    )
    if with_widgets:
        for name in ("busy_revealer", "busy_label", "activity_status_icon",
                     "activity_dismiss", "busy_spinner"):
            setattr(window, name, Widget())
    return window, actions


def test_no_gtk_import_and_stable_main_window_signatures():
    tree = ast.parse(OWNER.read_text(encoding="utf-8"))
    imports = [ast.unparse(n) for n in ast.walk(tree)
               if isinstance(n, (ast.ImportFrom, ast.Import))]
    assert not any(i == "import gi" or i.startswith("from gi ") or "main_window" in i for i in imports)
    window = next(n for n in ast.parse(MAIN.read_text(encoding="utf-8")).body
                  if isinstance(n, ast.ClassDef) and n.name == "MainWindow")
    methods = {n.name: n for n in window.body if isinstance(n, ast.FunctionDef)}
    for name, target in (
        ("_task_started", "task_started"),
        ("_task_progress", "task_progress"),
        ("_task_finished", "task_finished"),
    ):
        function = methods[name]
        assert isinstance(function.body[-1], ast.Return)
        call = function.body[-1].value
        assert isinstance(call, ast.Call)
        assert ast.unparse(call.func) == "task_activity_adapter." + target
        assert [a.arg for a in function.args.args][0] == "self"


def test_finished_before_started_cannot_resurrect_task_or_duplicate_history():
    window, actions = make_window()
    assert task_finished(window, 5, 0, "Prêt", True, None) is False
    assert task_started(window, 5, 1, "Prêt", 1, "sync") is False
    assert task_finished(window, 5, 0, "Prêt", True, None) is False
    assert not window._active_tasks
    assert window._finished_task_ids == {5}
    assert len(window.activity_feedback.rows()) == 1
    assert window.busy_label.label == "Terminé · Prêt"
    assert actions.count("reduce") == 1
    assert actions.count("history") == 1


def test_multiple_running_tasks_survive_stale_remaining_counter():
    window, actions = make_window()
    assert task_started(window, 1, 1, "Lent", 20, "sync") is False
    assert task_started(window, 2, 2, "Prioritaire", 1, "index") is False
    assert window.busy_label.label == "En cours · 2 opérations · Prioritaire"
    assert task_finished(window, 1, 0, "Lent", True, None) is False
    assert window.busy_label.label == "En cours · Prioritaire"
    assert len(window._active_tasks) == 1
    assert "reduce" not in actions
    assert task_progress(window, "Prioritaire 50 %") is False
    assert window.busy_label.label == "En cours · Prioritaire 50 %"
    assert task_finished(window, 2, 3, "Prioritaire", True, None) is False
    assert window.busy_label.label == "Terminé · Prioritaire"
    assert actions.count("reduce") == 1


def test_error_is_not_covered_by_later_success():
    window, actions = make_window()
    task_started(window, 1, 1, "Connexion", 1, "source")
    task_finished(window, 1, 0, "Connexion", False, "Échec réseau")
    assert window.activity_feedback.latest_unacknowledged_error() is not None
    assert "reduce" not in actions
    task_started(window, 2, 1, "Lecture", 1, "read")
    task_finished(window, 2, 0, "Lecture", True, None)
    assert window.activity_feedback.latest_unacknowledged_error().task_id == 1
    assert "reduce" not in actions
    assert len(window.activity_feedback.rows()) == 2


def test_closed_window_ignores_late_task_callbacks():
    window, actions = make_window()
    window._closed = True
    assert task_started(window, 1, 1, "Tardif", 1, "sync") is False
    assert task_progress(window, "Progression") is False
    assert task_finished(window, 1, 0, "Tardif", False, "Erreur") is False
    assert window._active_tasks == {}
    assert window._finished_task_ids == set()
    assert window.activity_feedback.rows() == ()
    assert actions == []


def test_headless_window_without_feedback_widgets_still_records_exactly_once():
    window, actions = make_window(with_widgets=False)
    task_started(window, 7, 1, "Sans GTK", 1, "io")
    task_finished(window, 7, 0, "Sans GTK", True, None)
    assert len(window.activity_feedback.rows()) == 1
    assert 7 in window._finished_task_ids
    assert "history" in actions
    assert "reduce" not in actions
