"""Headless notification and activity timer adapters for MainWindow (#1132).

MainWindow remains the owner of GTK widgets and GLib; the dispatcher supplies
timer functions to keep this module importable without PyGObject.
"""
from __future__ import annotations

from typing import Any, Callable

from .timeout_policy import bounded_seconds
from .timer_lifecycle import cancel_source


def cancel_notification_hide(window: Any, *, source_remove: Callable[[int], Any]) -> None:
    source = window._notification_hide_source
    window._notification_hide_source = None
    cancel_source(source, source_remove)


def hide_notification(window: Any) -> bool:
    window._notification_hide_source = None
    if getattr(window, "_closed", False):
        return False
    window.notification_revealer.set_reveal_child(False)
    return False


def dismiss_notification(window: Any) -> None:
    if getattr(window, "_closed", False):
        return
    window._cancel_notification_hide()
    window.notification_revealer.set_reveal_child(False)


def toast(
    window: Any, message: str, timeout: int = 3, *,
    timeout_add_seconds: Callable[[int, Callable[[], bool]], int],
) -> None:
    if getattr(window, "_closed", False):
        return
    window._cancel_notification_hide()
    window.notification_label.set_label(str(message))
    window.notification_revealer.set_reveal_child(True)
    bounded_timeout = bounded_seconds(timeout)
    window._notification_hide_source = timeout_add_seconds(
        bounded_timeout,
        window._hide_notification,
    )


def cancel_activity_hide(window: Any, *, source_remove: Callable[[int], Any]) -> None:
    source = window._activity_hide_source
    window._activity_hide_source = None
    cancel_source(source, source_remove)


def schedule_activity_reduce(
    window: Any, delay: int = 4, *,
    timeout_add_seconds: Callable[[int, Callable[[], bool]], int],
) -> None:
    if getattr(window, "_closed", False):
        return
    window._cancel_activity_hide()
    window._activity_hide_source = timeout_add_seconds(
        bounded_seconds(delay, maximum=3600, default=4),
        window._hide_activity_feedback,
    )
