"""P0 #1132: activity feedback view has no duplicate GTK or task state."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS

from cifereca.gui.activity_feedback import ActivityFeedback
from cifereca.gui.activity_history_projection import activity_history_rows
from cifereca.gui.activity_hide_policy import can_reduce_activity
from cifereca.gui.activity_feedback_view_controller import (
    dismiss_activity_feedback,
    hide_activity_feedback,
    refresh_activity_history,
    show_pending_activity_error,
)


ROOT = Path(__file__).resolve().parents[1] / "src/cifereca/gui"
OWNER = ROOT / "activity_feedback_view_controller.py"
MAIN = ROOT / "main_window.py"


class Widget:
    def __init__(self):
        self.calls = []
        self.classes = set()
        self.label = None

    def set_visible(self, value):
        self.calls.append(("visible", value))

    def set_label(self, value):
        self.label = value
        self.calls.append(("label", value))

    def set_reveal_child(self, value):
        self.calls.append(("reveal", value))

    def set_from_icon_name(self, value):
        self.calls.append(("icon", value))

    def add_css_class(self, name):
        self.classes.add(name)
        self.calls.append(("add", name))

    def remove_css_class(self, name):
        self.classes.discard(name)
        self.calls.append(("remove", name))

    def set_wrap(self, value):
        self.calls.append(("wrap", value))

    def stop(self):
        self.calls.append(("stop", None))


class Label(Widget):
    def __init__(self, *, label, xalign):
        super().__init__()
        self.label = label
        self.xalign = xalign


class Container:
    def __init__(self):
        self.children = []

    def append(self, child):
        self.children.append(child)


def context(*, widgets=True, history=True):
    window = NS(
        _closed=False, _active_tasks={}, _activity_hide_source=17,
        activity_feedback=ActivityFeedback(),
    )
    if widgets:
        for key in (
            "busy_spinner", "activity_status_icon", "busy_label",
            "activity_dismiss", "busy_revealer", "activity_history_button",
        ):
            setattr(window, key, Widget())
    if history:
        window.activity_history_box = Container()
    window._show_pending_activity_error = lambda: show_pending_activity_error(window)
    window._refresh_activity_history = lambda: refresh_activity_history(
        window, Gtk=NS(Label=Label),
        clear_box=lambda box: box.children.clear(),
        activity_history_rows=activity_history_rows,
    )
    window._hide_activity_feedback = lambda: hide_activity_feedback(
        window, can_reduce_activity=can_reduce_activity,
    )
    return window


def add_error(feedback, task_id, message):
    feedback.started(task_id, "Tâche", "test")
    feedback.finished(task_id, "Tâche", "test", success=False, error=message)


def test_owner_stays_headless_and_mainwindow_facades_preserve_signatures():
    source = OWNER.read_text(encoding="utf-8")
    ast.parse(source)
    assert "import gi" not in source and "main_window" not in source
    main = ast.parse(MAIN.read_text(encoding="utf-8"))
    cls = next(x for x in main.body if isinstance(x, ast.ClassDef)
               and x.name == "MainWindow")
    for old, new in (
        ("_refresh_activity_history", "refresh_activity_history"),
        ("_show_pending_activity_error", "show_pending_activity_error"),
        ("_hide_activity_feedback", "hide_activity_feedback"),
        ("_dismiss_activity_feedback", "dismiss_activity_feedback"),
    ):
        facade = next(x for x in cls.body if isinstance(x, ast.FunctionDef)
                      and x.name == old)
        assert [a.arg for a in facade.args.args] == ["self"]
        assert "activity_feedback_view_controller." + new in ast.unparse(facade)


def test_history_missing_widget_is_noop_without_reading_feedback():
    window = NS(_closed=False, activity_feedback=None)
    refresh_activity_history(
        window, Gtk=None, clear_box=lambda _: 1, activity_history_rows=lambda _: (),
    )


def test_history_projects_newest_first_and_only_errors_receive_warning():
    window = context()
    window.activity_feedback.started(1, "Lecture", "read")
    window.activity_feedback.finished(1, "Lecture", "read", success=True)
    add_error(window.activity_feedback, 2, "Erreur réseau")
    window._refresh_activity_history()
    labels = window.activity_history_box.children
    assert [x.label for x in labels] == [
        "Erreur · Erreur réseau", "Terminé · Lecture",
    ]
    assert "warning" in labels[0].classes
    assert "warning" not in labels[1].classes
    assert "caption" in labels[0].classes
    assert ("visible", True) in window.activity_history_button.calls


def test_history_refresh_replaces_rows_instead_of_duplicating_them():
    window = context()
    window.activity_feedback.started(3, "Tâche", "sync")
    window._refresh_activity_history()
    first = window.activity_history_box.children[0]
    window._refresh_activity_history()
    assert len(window.activity_history_box.children) == 1
    assert window.activity_history_box.children[0] is not first


def test_closed_history_and_closed_error_are_noops_without_widget_access():
    window = NS(_closed=True)
    refresh_activity_history(
        window, Gtk=None, clear_box=None, activity_history_rows=None,
    )
    assert show_pending_activity_error(window) is False


def test_missing_error_or_view_does_not_render_an_error_banner():
    window = context()
    assert show_pending_activity_error(window) is False
    assert window.busy_revealer.calls == []
    add_error(window.activity_feedback, 1, "Réseau")
    del window.busy_revealer
    assert show_pending_activity_error(window) is False
    assert window.busy_spinner.calls == []


def test_pending_error_displays_exact_latest_unacknowledged_error():
    window = context()
    add_error(window.activity_feedback, 1, "Première")
    add_error(window.activity_feedback, 2, "Deuxième")
    assert show_pending_activity_error(window) is True
    assert window.busy_label.label == "Erreur · Deuxième"
    assert ("icon", "dialog-warning-symbolic") in window.activity_status_icon.calls
    assert window.activity_status_icon.classes == {"activity-error"}
    assert ("visible", True) in window.activity_dismiss.calls
    assert ("reveal", True) in window.busy_revealer.calls
    window.activity_feedback.acknowledge_errors()
    assert show_pending_activity_error(window) is False


def test_live_task_prevents_reduction_and_clears_consumed_timeout_id():
    window = context()
    window._active_tasks[9] = (1, "Synchronisation", "source")
    assert window._hide_activity_feedback() is False
    assert window._activity_hide_source is None
    assert window.busy_revealer.calls == []
    assert window.busy_spinner.calls == []


def test_pending_error_wins_over_successful_reduction():
    window = context()
    add_error(window.activity_feedback, 1, "Droits refusés")
    assert window._hide_activity_feedback() is False
    assert window.busy_label.label == "Erreur · Droits refusés"
    assert window._activity_hide_source is None
    assert ("reveal", False) not in window.busy_revealer.calls


def test_finished_history_remains_accessible_when_success_collapses():
    window = context()
    window.activity_feedback.started(2, "Lecture", "read")
    window.activity_feedback.finished(2, "Lecture", "read", success=True)
    assert window._hide_activity_feedback() is False
    assert ("visible", False) in window.activity_status_icon.calls
    assert ("visible", True) in window.activity_history_button.calls
    assert ("reveal", True) in window.busy_revealer.calls
    assert window._activity_hide_source is None


def test_empty_history_collapses_activity_banner():
    window = context()
    assert window._hide_activity_feedback() is False
    assert ("visible", False) in window.activity_history_button.calls
    assert ("reveal", False) in window.busy_revealer.calls


def test_no_busy_widget_skips_collapse_but_clears_finished_timer():
    window = context(widgets=False)
    assert window._hide_activity_feedback() is False
    assert window._activity_hide_source is None


def test_timer_after_close_clears_ownership_without_touching_widgets():
    window = context()
    window._closed = True
    assert window._hide_activity_feedback() is False
    assert window._activity_hide_source is None
    assert all(
        getattr(window, name).calls == []
        for name in ("busy_spinner", "activity_status_icon", "busy_label")
    )


def test_dismissal_cancels_timer_acknowledges_errors_and_preserves_history():
    window = context()
    add_error(window.activity_feedback, 4, "Réseau absent")
    actions = []
    window._cancel_activity_hide = lambda: actions.append("cancel")
    window._refresh_activity_history = lambda: actions.append("history")
    window._hide_activity_feedback = lambda: actions.append("hide")
    dismiss_activity_feedback(window)
    assert actions == ["cancel", "history", "hide"]
    assert window.activity_feedback.latest_unacknowledged_error() is None
    assert len(window.activity_feedback.rows()) == 1


def test_closed_dismissal_neither_acks_errors_nor_cancels_timer():
    window = context()
    add_error(window.activity_feedback, 4, "Réseau absent")
    window._closed = True
    dismiss_activity_feedback(window)
    assert window.activity_feedback.latest_unacknowledged_error() is not None
    assert window._activity_hide_source == 17
