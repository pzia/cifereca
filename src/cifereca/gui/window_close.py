"""Headless coordinator for deterministic GTK window teardown (#1132)."""
from __future__ import annotations
from typing import Any, Callable


def close_window_resources(window: Any, *, source_remove: Callable[[int], Any]) -> bool:
    """Release existing owner handles once; do not start network operations."""
        if window._closed:
            return False
        window._closed = True
        for name in ("_settings_main_lifetime", "_settings_standalone_lifetime"):
            lifetime = getattr(window, name, None)
            if lifetime is not None:
                lifetime.dispose()
                setattr(window, name, None)
        window._diag_refresh = None
        try:
            window.config.set("ui.width", window.get_width())
            window.config.set("ui.height", window.get_height())
            window.config.save()
        except Exception:
            pass
        try:
            if window._kobo_backup_timer_id is not None:
                source_remove(window._kobo_backup_timer_id)
                window._kobo_backup_timer_id = None
        except Exception:
            pass
        try:
            window._cancel_activity_hide()
        except Exception:
            pass
        try:
            window._cached_book_unsubscribe()
        except Exception:
            pass
        try:
            window.runner.close()
        except Exception:
            pass
        try:
            window.history_cover_resolver.close()
        except Exception:
            pass
        try:
            window.covers.close()
        except Exception:
            pass
        try:
            window.service.close()
        except Exception:
            pass
        return False
