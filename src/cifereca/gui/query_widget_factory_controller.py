"""CoreUI #1129: compose search controls from public provider capabilities.

The owning MainWindow retains the services, plugin manager and widgets.
The fallback GTK QueryControls class is injected to keep this controller
importable without PyGObject and to preserve the optional widget service.
"""
from __future__ import annotations

from ..core.provider_identifier import valid_provider_identifier
from .provider_status_notices import search_status_notice


def _provider_choices(window, *, library=False):
    """Only live sources belong to Search; Library can filter historical books."""
    providers = dict(window.service.searchable_providers())
    providers["local"] = "Local"
    if library:
        for row in window.service.catalog_entries():
            provider = str(row.get("provider_id") or "local")
            providers.setdefault(provider, window.service.provider_display_name(provider))
        # Core's *already loaded*, identity-scoped Library projection can
        # contain several origins even when the catalogue has only the
        # primary/local provider ID. These are filter choices, never proof
        # of entitlement or permission to acquire a file.
        identity = getattr(getattr(window.service, "current_identity", None), "identity_id", None)
        if (type(identity) is str
                and getattr(window, "_library_origin_identity_id", None) == identity):
            for provider in getattr(window, "_library_origin_provider_ids", ()):
                providers.setdefault(provider, window.service.provider_display_name(provider))
    # The source filter is a *route selector* (stable provider ID), not a
    # purchase/loan filter. Keep only discovered routes; their labels come
    # directly from plugin descriptors, never from Core brand aliases.
    choices = list(providers.items())
    return sorted(choices, key=lambda pair: pair[1].casefold())



def _origin_ids_for_library_snapshot(entries, identity_id, *, all_profiles=False):
    """Bounded, exact-identity source IDs from a validated local Core snapshot.

    Ignore metadata hints, titles, SHA, foreign identities, and arbitrary
    provider values. This does not establish that the Core filter can match
    an OriginLink: that requires the separate Core #2797 increment.
    """
    if (type(identity_id) is not str or not identity_id
            or type(entries) not in (list, tuple) or len(entries) > 20000):
        return ()
    ids = set()
    for row in entries:
        if (type(row) is not dict
                or (not all_profiles and row.get("identity_id") != identity_id)):
            continue
        origins = row.get("providers")
        if type(origins) not in (list, tuple) or len(origins) > 32:
            continue
        for provider in origins:
            if valid_provider_identifier(provider):
                ids.add(provider)
    return tuple(sorted(ids))


def refresh_library_origin_choices(
    window, entries, *, identity_id, all_profiles=False,
):
    """One GTK-side update after the scoped async Library snapshot completed.

    Uses only provided in-memory rows. On profile switch old choices are
    ineligible because the frozen identity is checked before caching/display.
    """
    current = getattr(getattr(window.service, "current_identity", None), "identity_id", None)
    if (getattr(window, "_closed", False) or type(identity_id) is not str
            or not identity_id or identity_id != current):
        return
    choices = _origin_ids_for_library_snapshot(
        entries, identity_id, all_profiles=all_profiles is True,
    )
    window._library_origin_identity_id = identity_id
    window._library_origin_provider_ids = choices
    controls = getattr(window, "library_controls", None)
    setter = getattr(controls, "set_provider_choices", None)
    if not callable(setter):
        return
    try:
        setter(_provider_choices(window, library=True))
    except Exception:
        # Provider labels and plugin diagnostics can contain private data.
        diagnostics = getattr(window, "diagnostics", None)
        add = getattr(diagnostics, "add", None)
        if callable(add):
            try:
                add("PLUGIN", "library origin source choices", "error",
                    detail={"code": "library_origin_choices_unavailable"})
            except Exception:
                pass


def refresh_query_providers(window):
    """Refresh live controls after async provider connection without rebuilding pages."""
    if getattr(window, "_closed", False):
        return
    for route, library in (("search", False), ("library", True)):
        controls = getattr(window, route + "_controls", None)
        if controls is None:
            continue
        try:
            updater = getattr(controls, "set_provider_choices", None)
            if callable(updater):
                updater(_provider_choices(window, library=library))
            if not library:
                setter = getattr(controls, "set_provider_search_capabilities", None)
                if callable(setter):
                    setter(window.service.provider_search_capabilities())
                notice = getattr(controls, "set_provider_status_notice", None)
                if callable(notice):
                    notice(search_status_notice(window))
        except Exception:
            # A custom controls factory may fail during refresh. Do not let
            # plugin-provided error text abort account/dashboard startup.
            window.diagnostics.add(
                "PLUGIN", "query provider refresh", "error",
                detail={"code": "query_provider_refresh_failed"},
            )


def build_query_controls(window, changed, activate, *, library=False, QueryControls):
    """Resolve one widget factory, preserving current provider ordering and fallback."""
    providers = _provider_choices(window, library=library)
    catalog = window.plugin_manager.widget_catalog() if window.plugin_manager else None
    factory = getattr(catalog, "build_query_controls", None)
    controls = None
    if callable(factory):
        try:
            controls = factory(providers, changed, activate, library=library)
        except Exception:
            # A plugin factory may include private search URLs or settings
            # in its exception. The stable code is enough for Diagnostics.
            window.diagnostics.add(
                "PLUGIN", "query widget", "error",
                detail={"code": "query_widget_factory_failed"},
            )
    if controls is None:
        controls = QueryControls(providers, changed, activate, library=library)
    setter = getattr(controls, "set_provider_search_capabilities", None)
    if callable(setter) and not library:
        setter(window.service.provider_search_capabilities())
    notice = getattr(controls, "set_provider_status_notice", None)
    if callable(notice) and not library:
        notice(search_status_notice(window))
    return controls
