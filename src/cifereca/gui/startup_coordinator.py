"""Headless UI startup coordinator; MainWindow owns GTK and TaskRunner (#1132).
GLib scheduling is injected; no GTK import or background processing is added.
"""
from __future__ import annotations
import logging
import time
from functools import partial
from typing import Any
from ..task_queue import TaskPriority
from .startup_lifecycle import once_while_open
from .connection_failure_summary import connection_failure_summary
_TRACE_LOG = logging.getLogger("cifereca")


def connect_configured_sources(
    window, *, idle_add, timeout_add_seconds=None, source_remove=None,
) -> bool:
    if getattr(window, "_closed", False):
        return False
    _TRACE_LOG.info("TRACE gui connect-configured-sources begin")
    if hasattr(window, "startup_status"):
        window.startup_status.set_label("Connexion aux sources configurées…")
    if hasattr(window, "dashboard_loading_status"):
        window.dashboard_loading_status.set_label("Connexion aux sources en arrière-plan…")
        window.dashboard_loading_status.set_visible(True)

    def ok(result: dict[str, Any]) -> None:
        # A TaskRunner result may have been queued before close().
        # Detached GTK widgets must never receive its completion.
        if getattr(window, "_closed", False):
            return
        _TRACE_LOG.info("TRACE gui connect-configured ok connected=%s errors=%s", len(result.get("connected", [])), len(result.get("errors", {}) or {}))
        if hasattr(window, "dashboard_loading_status"):
            window.dashboard_loading_status.set_visible(False)
        _TRACE_LOG.info("TRACE gui populate-accounts begin")
        window._populate_accounts()
        # Providers finish connecting after the Search page was constructed.
        # Update choices on GTK's completion callback, not from a worker.
        from .query_widget_factory_controller import refresh_query_providers
        refresh_query_providers(window)
        if getattr(window, "_closed", False):
            return
        # Refresh already-built Synchronisation cards after asynchronous
        # login. No provider I/O: the GTK callback reads Core's in-memory
        # accounts plus the identity-scoped configured-only notices (#3105).
        if getattr(window, "_sync_sources_box", None) is not None:
            refresh_sync = getattr(window, "_refresh_sync_page", None)
            if callable(refresh_sync):
                refresh_sync()
        if getattr(window, "_closed", False):
            return
        _TRACE_LOG.info("TRACE gui populate-accounts end")
        _TRACE_LOG.info("TRACE gui populate-loans begin count=%s", len(window.service.last_loans))
        window._populate_loans(window.service.last_loans)
        _TRACE_LOG.info("TRACE gui populate-loans end")
        connected = len(result.get("connected", []))
        errors = result.get("errors", {}) or {}
        safe_details = connection_failure_summary(result.get("connection_failures"))
        status = f"{connected} source(s) connectée(s)"
        if errors:
            status += f", {len(errors)} en erreur"
            if safe_details:
                status += ". " + safe_details
        window._activity_result(
            status,
            success=not bool(errors),
            persistent=bool(errors),
        )
        window._render_dashboard()
        # A close can happen after the connection callback but before GTK
        # dispatches this idle source. Never start sync on a dead window.
        def initial_opening_sync():
            # A one-shot marker, consumed synchronously by run_opening_sync.
            # A later user command with control=None must never inherit
            # startup-only consent from this particular connection callback.
            window._source_inventory_startup_pending = True
            try:
                window.run_opening_sync()
            finally:
                window._source_inventory_startup_pending = False
        if not getattr(window, "_source_inventory_startup_connection_seen", False):
            window._source_inventory_startup_connection_seen = True
            idle_add(once_while_open(window, initial_opening_sync))
        else:
            # A user-triggered reconnect is not an application startup, even
            # when no source happened to be due on the first connection.
            idle_add(once_while_open(window, window.run_opening_sync))
        # No periodic poll is registered unless a Kobo source has a strict
        # per-source inventory opt-in. This is separate from startup backup
        # and does not mutate the legacy global acquisition timer.
        if timeout_add_seconds is not None and source_remove is not None:
            from . import source_inventory_timer
            def schedule_inventory_timer():
                source_inventory_timer.configure(
                    window, idle_add=idle_add,
                    timeout_add_seconds=timeout_add_seconds,
                    source_remove=source_remove,
                )
            idle_add(once_while_open(window, schedule_inventory_timer))

    def err(exc: BaseException) -> None:
        if getattr(window, "_closed", False):
            return
        if hasattr(window, "startup_status"):
            # Provider errors can include credentials or signed URLs.
            window.startup_status.set_label("Connexion aux sources indisponible.")
        if hasattr(window, "dashboard_loading_status"):
            window.dashboard_loading_status.set_label("Connexion aux sources indisponible. La navigation locale reste disponible.")
            window.dashboard_loading_status.set_visible(True)

    control = getattr(window, "startup_connect_button", None)
    # Capture the *known* caller on the GTK side before dispatching the
    # worker. The very first UI connection belongs to startup; subsequent
    # user-requested re-connections are explicit refreshes. Never infer the
    # caller from log timestamps, provider messages or a worker thread.
    caller_category = (
        "explicit_refresh" if getattr(
            window, "_source_inventory_startup_connection_seen", False
        ) is True else "startup/connect_configured"
    )
    # The dedicated Core facade accepts this audited marker. Historical
    # test/plugin service doubles may expose only connect_configured(); do
    # not force a new argument into that established public method.
    connect_with_caller = getattr(window.service, "connect_configured_with_caller", None)
    connect = (
        partial(connect_with_caller, caller_category=caller_category)
        if callable(connect_with_caller)
        else window.service.connect_configured
    )
    window.runner.submit(
        "Connexion aux providers…", connect, ok, err, control=control,
        priority=TaskPriority.PROVIDER_SYNC, kind="provider-connect",
    )
    return False


def activate_startup_plugins(window, *, idle_add) -> bool:
    # Activation is posted to GTK after the window is presented. That
    # queued callback may fire after the user has closed the window.
    if getattr(window, "_closed", False):
        return False
    if window.plugin_manager is None:
        # A local-only/no-plugin installation must still connect its
        # configured providers; plugin activation is optional.
        idle_add(window._connect_sources_if_open)
        return False
    _TRACE_LOG.info("TRACE gui plugin-activation begin")
    started = time.perf_counter()
    try:
        window.plugin_manager.activate()
        if getattr(window, "_closed", False):
            return False
        window.service.attach_plugin_manager(window.plugin_manager)
        if getattr(window, "_closed", False):
            return False
        window._plugins_changed()
    except Exception:
        if not getattr(window, "_closed", False):
            # Plugin errors may contain URLs, account IDs or tokens.
            window.diagnostics.add("PLUGIN", "startup activation", "error", detail={"reason": "activation_failed"})
            if hasattr(window, "dashboard_loading_status"):
                window.dashboard_loading_status.set_label("Certains modules n’ont pas pu être chargés. Le cœur reste disponible.")
                window.dashboard_loading_status.set_visible(True)
    finally:
        # Provider connection must not depend on successful page/header
        # reconstruction. A CoreUI exception must never strand startup in
        # local-only mode after plugins have been activated.
        if not getattr(window, "_closed", False):
            _TRACE_LOG.info("TRACE gui schedule connect-configured")
            idle_add(window._connect_sources_if_open)
        duration_ms = (time.perf_counter() - started) * 1000.0
        window.ui_perf.record("startup.plugins_activation", duration_ms)
        _TRACE_LOG.info("TRACE gui plugin-activation end duration_ms=%.1f", duration_ms)
    return False
