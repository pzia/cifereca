"""P0 #1132: no GTK callbacks or new tasks after MainWindow teardown."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS

from cifereca.gui.window_close import close_window_resources
from cifereca.gui import feedback_timers


ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "src/cifereca/gui/main_window.py"
RUNNER = ROOT / "src/cifereca/gui/task_runner.py"


def _unbound_method(path: Path, owner: str, method_name: str):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    cls = next(x for x in tree.body
               if isinstance(x, ast.ClassDef) and x.name == owner)
    function = next(x for x in cls.body
                    if isinstance(x, ast.FunctionDef) and x.name == method_name)
    future = ast.ImportFrom(
        module="__future__", names=[ast.alias(name="annotations")], level=0,
    )
    module = ast.fix_missing_locations(ast.Module(
        body=[future, function], type_ignores=[],
    ))
    namespace = {"TaskPriority": NS(NORMAL=0), "feedback_timers": feedback_timers}
    exec(compile(module, str(path), "exec"), namespace)
    return namespace[method_name]


def test_late_ui_callbacks_are_noops_after_close():
    host = NS(_closed=True)
    for name in (
        "toast", "show_error", "show_attention",
        "_dismiss_notification", "_dismiss_activity_feedback",
        "_schedule_activity_reduce", "_refresh_activity_history",
        "_activity_result",
    ):
        method = _unbound_method(MAIN, "MainWindow", name)
        if name in ("toast", "show_error", "show_attention", "_activity_result"):
            assert method(host, "late result") is None
        else:
            assert method(host) is None

    for name in (
        "_hide_notification", "_hide_activity_feedback",
        "_show_pending_activity_error", "_task_progress",
    ):
        method = _unbound_method(MAIN, "MainWindow", name)
        if name == "_task_progress":
            assert method(host, "late") is False
        else:
            assert method(host) is False


def test_timeout_callbacks_clear_source_ownership_without_touching_widgets():
    host = NS(_closed=True, _notification_hide_source=123, _activity_hide_source=456)
    assert _unbound_method(MAIN, "MainWindow", "_hide_notification")(host) is False
    assert _unbound_method(MAIN, "MainWindow", "_hide_activity_feedback")(host) is False
    assert host._notification_hide_source is None
    assert host._activity_hide_source is None


def test_task_submission_after_close_leaves_control_and_queue_untouched():
    submit = _unbound_method(RUNNER, "TaskRunner", "submit")
    class Control:
        def set_sensitive(self, _value):
            raise AssertionError("detached control accessed")
    runner = NS(window=NS(_closed=True))
    assert submit(runner, "late operation", lambda: 1, control=Control()) is None


def test_window_close_cancels_toast_timeout_once_before_closing_services():
    calls = []
    config = NS(
        set=lambda *args: calls.append("save-setting"),
        save=lambda: calls.append("config-save"),
    )
    window = NS(
        _closed=False, _settings_main_lifetime=None,
        _settings_standalone_lifetime=None, _diag_refresh="pending",
        config=config, get_width=lambda: 800, get_height=lambda: 600,
        _kobo_backup_timer_id=None,
        _cancel_notification_hide=lambda: calls.append("cancel-toast"),
        _cancel_activity_hide=lambda: calls.append("cancel-activity"),
        _cached_book_unsubscribe=lambda: calls.append("unsubscribe"),
        runner=NS(close=lambda: calls.append("runner-close")),
        history_cover_resolver=NS(close=lambda: calls.append("covers-history-close")),
        covers=NS(close=lambda: calls.append("covers-close")),
        service=NS(close=lambda: calls.append("service-close")),
    )
    assert close_window_resources(window, source_remove=lambda _id: None) is False
    assert window._closed is True
    assert window._diag_refresh is None
    assert calls.count("cancel-toast") == 1
    assert calls.index("cancel-toast") < calls.index("runner-close")
    before = list(calls)
    assert close_window_resources(window, source_remove=lambda _id: None) is False
    assert calls == before
