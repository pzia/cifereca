"""Single-window activity history and pending error presenters (#1132).

No imported GTK and no duplicate activity state, timers or signal subscribers.
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
    window.busy_spinner.stop(); window.busy_spinner.set_visible(False)
    window.activity_status_icon.set_visible(True)
    window.activity_status_icon.set_from_icon_name("dialog-warning-symbolic")
    window.activity_status_icon.add_css_class("activity-error")
    window.activity_status_icon.remove_css_class("activity-success")
    window.busy_label.set_visible(True)
    window.busy_label.set_label(entry.display_text)
    window.activity_dismiss.set_visible(True)
    window.busy_revealer.set_reveal_child(True)
    return True
