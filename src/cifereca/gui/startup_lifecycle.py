"""Safe one-shot startup callbacks independent of GTK (#1132)."""

from __future__ import annotations

from typing import Callable, Any


def once_while_open(window: Any, operation: Callable[[], Any]) -> Callable[[], bool]:
    """Return an idle callback that does not run after window teardown.

    Always return False so GLib cannot reschedule the callback.
    """
    def dispatch() -> bool:
        if not getattr(window, "_closed", False):
            operation()
        return False

    return dispatch
