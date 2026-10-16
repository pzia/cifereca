"""GTK-free adapter for activity history, error priority and dismissal (#1132).

The window continues to own ActivityFeedback, widgets and timers; Gtk and the
existing pure projection/reduction policy are supplied by the MainWindow facade.
No new subscriptions, timers, history stores, or acknowledgement policy.
"""
from __future__ import annotations


def refresh_activity_history(window, *, Gtk, clear_box, activity_history_rows) -> None:
    if getattr(window, "_closed", False):
        return
    if not hasattr(window, "activity_history_box"):
        return
    clear_box(window.activity_history_box)
    rows = activity_history_rows(window.activity_feedback.rows())
    for display_text, is_error in rows:
        label = Gtk.Label(label=display_text, xalign=0)
        label.set_wrap(True)
        label.add_css_class("caption")
        if is_error:
            label.add_css_class("warning")
        window.activity_history_box.append(label)
    window.activity_history_button.set_visible(bool(rows))


def show_pending_activity_error(window) -> bool:
    if getattr(window, "_closed", False):
        return False
    entry = window.activity_feedback.latest_unacknowledged_error()
    if entry is None or not hasattr(window, "busy_revealer"):
        return False
    current = window.activity_feedback.latest_completed()
    earlier = current is not None and current.state == 'success' and current.sequence > entry.sequence
    window.busy_spinner.stop()
    window.busy_spinner.set_visible(False)
    window.activity_status_icon.set_visible(True)
    window.activity_status_icon.set_from_icon_name('emblem-ok-symbolic' if earlier else 'dialog-warning-symbolic')
    window.activity_status_icon.add_css_class('activity-success' if earlier else 'activity-error')
    window.activity_status_icon.remove_css_class('activity-error' if earlier else 'activity-success')
    window.busy_label.set_visible(True)
    age = window.activity_feedback.error_age(entry)
    if earlier:
        text = f'{current.display_text}\nErreur antérieure · {entry.operation_label} · {age} · {entry.message}'
    else:
        text = f'{entry.display_text} · {age}'
    window.busy_label.set_label(text)
    window.activity_dismiss.set_visible(True)
    window.busy_revealer.set_reveal_child(True)
    return True


def hide_activity_feedback(window, *, can_reduce_activity) -> bool:
    # The GLib source is no longer pending even when teardown or running work
    # makes the timer callback a no-op.
    window._activity_hide_source = None
    if getattr(window, "_closed", False) or window._active_tasks:
        return False
    pending_error = window._show_pending_activity_error()
    if not can_reduce_activity(
        closed=False, active_count=0, pending_error=pending_error,
        has_view=hasattr(window, "busy_revealer"),
    ):
        return False
    window.busy_spinner.stop()
    window.busy_spinner.set_visible(False)
    window.activity_status_icon.set_visible(False)
    window.activity_dismiss.set_visible(False)
    rows = window.activity_feedback.rows()
    window.busy_label.set_visible(False)
    window.activity_history_button.set_visible(bool(rows))
    window.busy_revealer.set_reveal_child(bool(rows))
    return False


def dismiss_activity_feedback(window) -> None:
    if getattr(window, "_closed", False):
        return
    window._cancel_activity_hide()
    window.activity_feedback.acknowledge_errors()
    window._refresh_activity_history()
    window._hide_activity_feedback()
