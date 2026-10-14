"""UI task feedback adapter independent of GTK (#1132).

TaskRunner still dispatches on the window's GTK main loop. The window owns
widgets and lifetime; state and presentation policy remain unchanged.
"""
from __future__ import annotations

from .task_state import register_task_start, register_task_finish
from .task_progress_presentation import live_task_progress_label


def task_started(window, task_id: int, count: int, label: str, priority: int, kind: str) -> bool:
    if getattr(window, '_closed', False):return False
    if not register_task_start(
        window._active_tasks, window._finished_task_ids,
        task_id, priority, label, kind,
    ):
        return False
    window._cancel_activity_hide()
    window.activity_feedback.started(int(task_id), str(label), str(kind))
    window._refresh_activity_history()
    if hasattr(window, "busy_revealer"):
        window.activity_status_icon.set_visible(False)
        window.activity_status_icon.remove_css_class("activity-success")
        window.activity_status_icon.remove_css_class("activity-error")
        window.activity_dismiss.set_visible(False)
        window.busy_label.set_visible(True)
        active_count = len(window._active_tasks)
        prefix = f"{active_count} opérations · " if active_count > 1 else ""
        window.busy_label.set_label("En cours · " + prefix + str(label))
        window.busy_revealer.set_reveal_child(True)
        window.busy_spinner.set_visible(True)
        window.busy_spinner.start()
    return False


def task_progress(window, label: str, *, task_id: int | None = None) -> bool:
    if getattr(window, "_closed", False):
        return False
    if hasattr(window, "busy_revealer") and window._active_tasks:
        presentation = live_task_progress_label(window._active_tasks, label, task_id=task_id)
        if presentation is not None:
            window.busy_label.set_visible(True)
            window.busy_label.set_label(presentation)
    return False


def task_finished(window, task_id: int, remaining: int, label: str, success: bool, error: str | None) -> bool:
    if getattr(window, '_closed', False):return False
    task_id = int(task_id)
    # TaskRunner may deliver completion before the queued "started" UI
    # callback. Record it once and ignore any repeated completion, so a
    # late callback cannot resurrect a finished task or duplicate a toast.
    accepted, kind = register_task_finish(
        window._active_tasks, window._finished_task_ids, task_id,
    )
    if not accepted:
        return False
    window._last_finished_task_id = int(task_id)
    functional = window._functional_error_message(error or "") if not success else ""
    window.activity_feedback.finished(
        int(task_id),
        str(label),
        str(kind),
        success=bool(success),
        error=functional,
    )
    window._refresh_activity_history()
    if not hasattr(window, "busy_revealer"):
        return False
    if window._active_tasks:
        _priority, next_label, _kind = sorted(window._active_tasks.values(), key=lambda row: row[0])[0]
        active_count = len(window._active_tasks)
        prefix = f"{active_count} opérations · " if active_count > 1 else ""
        window.busy_label.set_visible(True)
        window.busy_label.set_label("En cours · " + prefix + next_label)
        window.activity_dismiss.set_visible(False)
        window.busy_spinner.set_visible(True)
        window.busy_spinner.start()
        return False
    if window._show_pending_activity_error():
        return False
    window.busy_spinner.stop()
    window.busy_spinner.set_visible(False)
    window.activity_status_icon.set_visible(True)
    window.activity_status_icon.set_from_icon_name("emblem-ok-symbolic")
    window.activity_status_icon.add_css_class("activity-success")
    window.activity_status_icon.remove_css_class("activity-error")
    window.busy_label.set_visible(True)
    window.busy_label.set_label(f"Terminé · {label}")
    window.activity_dismiss.set_visible(False)
    window.busy_revealer.set_reveal_child(True)
    window._schedule_activity_reduce()
    return False
