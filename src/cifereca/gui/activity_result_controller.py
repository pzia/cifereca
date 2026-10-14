"""Present a TaskRunner result in the sole activity banner (#1132).

Window-owned widgets, task ids and feedback history are not copied to a
parallel controller; caller closures remain guarded against late delivery.
"""
from __future__ import annotations


def activity_result(
    window,
    message: str,
    *,
    success: bool = True,
    persistent: bool = False,
    task_id: int | None = None,
) -> None:
    if getattr(window, "_closed", False):
        return
    resolved_id = int(task_id) if task_id is not None else window._last_finished_task_id
    if resolved_id is None:
        return
    window.activity_feedback.result(resolved_id, str(message), success=bool(success))
    window._refresh_activity_history()
    if window._active_tasks or not hasattr(window, "busy_revealer"):
        return
    if not success:
        window._show_pending_activity_error()
        return
    # A later success must never mask an earlier error that the user has
    # not acknowledged yet.
    if window._show_pending_activity_error():
        return
    window.busy_spinner.stop(); window.busy_spinner.set_visible(False)
    window.activity_status_icon.set_visible(True)
    window.activity_status_icon.set_from_icon_name("emblem-ok-symbolic")
    window.activity_status_icon.add_css_class("activity-success")
    window.activity_status_icon.remove_css_class("activity-error")
    window.busy_label.set_visible(True)
    window.busy_label.set_label(f"Terminé · {message}")
    window.activity_dismiss.set_visible(bool(persistent))
    window.busy_revealer.set_reveal_child(True)
    if not persistent:
        window._schedule_activity_reduce()