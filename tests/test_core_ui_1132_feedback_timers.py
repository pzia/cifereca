"""#1132: headless notification timer and activity lifetime contracts."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS

from cifereca.gui import feedback_timers as timers

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "src/cifereca/gui/main_window.py"
TIMERS = ROOT / "src/cifereca/gui/feedback_timers.py"


class View:
    def __init__(self, label=None):
        self.values = []
        self.label = label

    def set_label(self, value):
        self.values.append(value)

    def set_reveal_child(self, value):
        self.values.append(value)


def host():
    label, revealer = View(), View()
    window = NS(
        _closed=False,
        _notification_hide_source=None,
        _activity_hide_source=None,
        notification_label=label,
        notification_revealer=revealer,
    )
    removed = []
    window._cancel_notification_hide = lambda: timers.cancel_notification_hide(
        window, source_remove=removed.append
    )
    window._hide_notification = lambda: timers.hide_notification(window)
    window._cancel_activity_hide = lambda: timers.cancel_activity_hide(
        window, source_remove=removed.append
    )
    window._hide_activity_feedback = lambda: False
    return window, label, revealer, removed


def test_adapter_is_gtk_free_and_facades_keep_glib_at_owner_boundary():
    code = TIMERS.read_text(encoding="utf-8")
    tree = ast.parse(code)
    imports = [ast.unparse(node) for node in ast.walk(tree)
               if isinstance(node, (ast.Import, ast.ImportFrom))]
    assert not any("gi" in item or "main_window" in item for item in imports)
    cls = next(node for node in ast.parse(MAIN.read_text(encoding="utf-8")).body
               if isinstance(node, ast.ClassDef) and node.name == "MainWindow")
    methods = {node.name: ast.unparse(node) for node in cls.body
               if isinstance(node, ast.FunctionDef)}
    for name in (
        "_cancel_notification_hide", "_hide_notification",
        "_dismiss_notification", "toast",
        "_cancel_activity_hide", "_schedule_activity_reduce",
    ):
        assert "feedback_timers." in methods[name]
    assert "source_remove=GLib.source_remove" in methods["_cancel_notification_hide"]
    assert "source_remove=GLib.source_remove" in methods["_cancel_activity_hide"]
    assert "timeout_add_seconds=GLib.timeout_add_seconds" in methods["toast"]
    assert "timeout_add_seconds=GLib.timeout_add_seconds" in methods["_schedule_activity_reduce"]


def test_two_toasts_replace_single_timer_without_creating_new_widgets():
    window, label, revealer, removed = host()
    scheduled = []
    def schedule(seconds, callback):
        scheduled.append((seconds, callback))
        return len(scheduled) + 100

    timers.toast(window, "premier", 400, timeout_add_seconds=schedule)
    assert window._notification_hide_source == 101
    assert scheduled[0][0] == 10
    timers.toast(window, "second", 2, timeout_add_seconds=schedule)
    assert removed == [101]
    assert window._notification_hide_source == 102
    assert label.values == ["premier", "second"]
    assert revealer.values == [True, True]
    assert scheduled[1][0] == 2
    assert scheduled[1][1]() is False
    assert window._notification_hide_source is None
    assert revealer.values[-1] is False


def test_dismiss_cancels_timer_before_hiding():
    window, _, revealer, removed = host()
    window._notification_hide_source = 93
    timers.dismiss_notification(window)
    assert removed == [93]
    assert window._notification_hide_source is None
    assert revealer.values == [False]
    timers.dismiss_notification(window)
    assert removed == [93]


def test_close_blocks_new_timeouts_and_delayed_widget_access():
    window, label, revealer, removed = host()
    window._notification_hide_source = 7
    window._activity_hide_source = 8
    window._closed = True
    def forbidden(*_args):
        raise AssertionError("scheduled after closure")
    timers.toast(window, "tardif", timeout_add_seconds=forbidden)
    timers.schedule_activity_reduce(window, timeout_add_seconds=forbidden)
    timers.dismiss_notification(window)
    assert timers.hide_notification(window) is False
    assert label.values == []
    assert revealer.values == []
    assert window._notification_hide_source is None
    assert window._activity_hide_source == 8  # cleanup remains the window close owner's task
    timers.cancel_notification_hide(window, source_remove=removed.append)
    timers.cancel_activity_hide(window, source_remove=removed.append)
    assert removed == [8]


def test_timer_removal_failure_still_discards_ownership():
    window, _, _, _ = host()
    window._notification_hide_source = 17
    window._activity_hide_source = 21
    def fail(_source):
        raise RuntimeError("already dispatched")
    timers.cancel_notification_hide(window, source_remove=fail)
    timers.cancel_activity_hide(window, source_remove=fail)
    assert window._notification_hide_source is None
    assert window._activity_hide_source is None


def test_activity_reduction_is_bounded_and_reschedules_once():
    window, _, _, removed = host()
    scheduled = []
    def schedule(seconds, callback):
        scheduled.append((seconds, callback))
        return 200 + len(scheduled)
    timers.schedule_activity_reduce(window, 7200, timeout_add_seconds=schedule)
    assert scheduled[0][0] == 3600
    assert window._activity_hide_source == 201
    timers.schedule_activity_reduce(window, "invalid", timeout_add_seconds=schedule)
    assert removed == [201]
    assert scheduled[1][0] == 4
    assert window._activity_hide_source == 202


def test_scheduled_callbacks_use_current_window_methods_not_old_widget_refs():
    window, _, _, _ = host()
    callbacks = []
    def schedule(seconds, callback):
        callbacks.append(callback)
        return len(callbacks)
    timers.toast(window, "test", timeout_add_seconds=schedule)
    timers.schedule_activity_reduce(window, timeout_add_seconds=schedule)
    # The callback identity is always the current window's facade.
    assert callbacks[0] == window._hide_notification
    assert callbacks[1] == window._hide_activity_feedback
