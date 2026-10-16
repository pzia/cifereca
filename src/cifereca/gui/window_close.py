"""Headless coordinator for deterministic GTK window teardown (#1132)."""
from __future__ import annotations
from typing import Any, Callable


def close_window_resources(window: Any, *, source_remove: Callable[[int], Any]) -> bool:
    """Release existing owner handles once; do not start network operations."""
    if window._closed:
        return False
    window._closed = True
    frame_probe = getattr(window, "_ui_perf_frame_probe", None)
    window._ui_perf_frame_probe = None
    if frame_probe is not None:
        try:
            frame_probe.dispose()
        except Exception:
            # No asynchronous GDK callback may survive window teardown.
            pass
    for name in ("_settings_main_lifetime", "_settings_standalone_lifetime"):
        lifetime = getattr(window, name, None)
        if lifetime is None:
            continue
        try:
            lifetime.dispose()
        except Exception:
            # A broken plugin callback during one page's teardown must not
            # prevent releasing the other lifetime, subscriptions, worker
            # pool or the backing service. The window is already closed.
            pass
        finally:
            setattr(window, name, None)
    window._diag_refresh = None
    try:
        window.config.set("ui.width", window.get_width())
        window.config.set("ui.height", window.get_height())
        window.config.save()
    except Exception:
        pass
    # Cancel periodic inventory heartbeat AND unsubscribe from broker
    # notifications before tearing down the worker pool and service. A queued
    # GTK idle callback additionally checks the already-closed owner.
    from . import source_inventory_timer
    source_inventory_timer.stop(window, source_remove=source_remove)
    timer_id = getattr(window, "_kobo_backup_timer_id", None)
    window._kobo_backup_timer_id = None
    if timer_id is not None:
        try:
            source_remove(timer_id)
        except Exception:
            # The source may already have been dispatched or removed.
            pass
    try:
        # A notification timeout can already be queued when GTK is closed.
        # Remove the source as well as guarding its eventual callback.
        window._cancel_notification_hide()
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
    finally:
        # Drop plugin/service references retained by stale callbacks.
        window._cached_book_unsubscribe = lambda: None
        window._cached_book_services = ()
        window._cached_book_context = None
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
