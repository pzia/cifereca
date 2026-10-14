"""P0 #1132 — one activity result banner, safe task correlation."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

from cifereca.gui.activity_result_controller import activity_result


MAIN = Path(__file__).resolve().parents[1] / "src/cifereca/gui/main_window.py"
OWNER = MAIN.with_name("activity_result_controller.py")


class Widget:
    def __init__(self):
        self.events = []

    def __getattr__(self, name):
        if name.startswith(("set_", "add_", "remove_")) or name in {"stop", "start"}:
            return lambda *args: self.events.append((name, *args))
        raise AttributeError(name)


def _window(*, active=None, pending=False, task_id=9):
    events = []
    widgets = {name: Widget() for name in (
        "busy_spinner", "activity_status_icon", "busy_label",
        "activity_dismiss", "busy_revealer",
    )}
    result = Mock(side_effect=lambda *args, **kwargs: events.append(
        ("result", args, kwargs)
    ))
    window = NS(
        _closed=False,
        _last_finished_task_id=task_id,
        _active_tasks={} if active is None else active,
        activity_feedback=NS(result=result),
        _refresh_activity_history=lambda: events.append(("history",)),
        _show_pending_activity_error=lambda: (
            events.append(("pending",)) or pending
        ),
        _schedule_activity_reduce=lambda: events.append(("reduce",)),
        **widgets,
    )
    return window, events, widgets


def test_gtk_free_owner_and_preserved_keyword_facade():
    source = ast.parse(OWNER.read_text(encoding="utf-8"))
    assert not any(isinstance(node, (ast.ImportFrom, ast.Import))
                   and ("gi" in ast.unparse(node) or "main_window" in ast.unparse(node))
                   for node in ast.walk(source))
    main = ast.parse(MAIN.read_text(encoding="utf-8"))
    cls = next(node for node in main.body if isinstance(node, ast.ClassDef)
               and node.name == "MainWindow")
    facade = next(node for node in cls.body if isinstance(node, ast.FunctionDef)
                  and node.name == "_activity_result")
    assert [a.arg for a in facade.args.args] == ["self", "message"]
    assert [a.arg for a in facade.args.kwonlyargs] == [
        "success", "persistent", "task_id",
    ]
    assert "activity_result_controller.activity_result" in ast.unparse(facade)


def test_closed_window_never_reaches_disposed_widgets_or_activity_history():
    assert activity_result(NS(_closed=True), "late") is None


def test_no_finished_task_id_is_inert_and_does_not_guess_task_identity():
    window, events, widgets = _window(task_id=None)
    activity_result(window, "not tied to a task")
    assert events == []
    assert all(not widget.events for widget in widgets.values())


def test_explicit_task_id_zero_is_valid_and_tracks_only_its_own_result():
    window, events, widgets = _window(task_id=None, active={7: object()})
    activity_result(window, "opération", task_id=0, success=False)
    assert events[:2] == [
        ("result", (0, "opération"), {"success": False}),
        ("history",),
    ]
    assert len(events) == 2
    assert all(not widget.events for widget in widgets.values())


def test_later_success_never_masks_earlier_unacknowledged_error():
    window, events, widgets = _window(pending=True)
    activity_result(window, "dernière réussite")
    assert [event[0] for event in events] == ["result", "history", "pending"]
    assert not widgets["busy_label"].events
    assert not widgets["busy_revealer"].events
    assert "reduce" not in events


def test_failure_has_priority_over_success_styling_and_auto_dismiss():
    window, events, widgets = _window()
    activity_result(window, "échec", success=False, persistent=True)
    assert events[:3] == [
        ("result", (9, "échec"), {"success": False}),
        ("history",), ("pending",),
    ]
    assert not widgets["busy_label"].events
    assert "reduce" not in events


def test_live_parallel_work_keeps_running_indicator_and_history():
    window, events, widgets = _window(active={1: object()})
    activity_result(window, "partie achevée", task_id=4)
    assert events == [
        ("result", (4, "partie achevée"), {"success": True}),
        ("history",),
    ]
    assert all(not widget.events for widget in widgets.values())


def test_success_uses_one_banner_then_schedules_reduction():
    window, events, widgets = _window()
    activity_result(window, "Terminé le transfert")
    assert [e[0] for e in events] == ["result", "history", "pending", "reduce"]
    assert ("set_label", "Terminé · Terminé le transfert") in widgets["busy_label"].events
    assert ("set_reveal_child", True) in widgets["busy_revealer"].events
    assert ("set_visible", False) in widgets["activity_dismiss"].events
    assert ("set_from_icon_name", "emblem-ok-symbolic") in widgets["activity_status_icon"].events
    assert ("add_css_class", "activity-success") in widgets["activity_status_icon"].events


def test_persistent_success_stays_visible_until_user_dismissal():
    window, events, widgets = _window()
    activity_result(window, "Résultat explicite", persistent=True)
    assert ("set_visible", True) in widgets["activity_dismiss"].events
    assert "reduce" not in events
