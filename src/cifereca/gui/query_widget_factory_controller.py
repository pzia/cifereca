"""CoreUI #1129: compose search controls from public provider capabilities.

The owning MainWindow retains the services, plugin manager and widgets.
The fallback GTK QueryControls class is injected to keep this controller
importable without PyGObject and to preserve the optional widget service.
"""
from __future__ import annotations


def _provider_choices(window, *, library=False):
    """Only live sources belong to Search; Library can filter historical books."""
    providers = dict(window.service.searchable_providers())
    providers["local"] = "Local"
    if library:
        for row in window.service.catalog_entries():
            provider = str(row.get("provider_id") or "local")
            providers.setdefault(provider, window.service.provider_display_name(provider))
    return sorted(providers.items(), key=lambda pair: pair[1])


def _paris_catalogue_hint(window):
    """Read-only, identity-scoped status; never makes an offline source searchable."""
    service = window.service
    identity = getattr(getattr(service, "current_identity", None), "identity_id", None)
    reader = getattr(service, "catalogue_source_capabilities", None)
    if not isinstance(identity, str) or not identity or not callable(reader):
        return ""
    try:
        rows = reader(identity_id=identity)
        if not isinstance(rows, (tuple, list)) or len(rows) > 128:
            return ""
        for row in rows:
            if (getattr(row, "provider_id", None) == "paris"
                    and getattr(row, "catalog_search_supported", None) is True
                    and getattr(row, "catalog_search_available", None) is not True):
                return (
                    "Bibliothèques de Paris : catalogue connu, mais recherche en "
                    "ligne indisponible pour ce profil. Vérifier la connexion "
                    "dans Synchronisation."
                )
    except Exception:
        # No provider payload or credentials in the UI/diagnostics.
        return ""
    return ""


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
                hint = getattr(controls, "set_provider_availability_hint", None)
                if callable(hint):
                    hint(_paris_catalogue_hint(window))
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
    if not library:
        hint = getattr(controls, "set_provider_availability_hint", None)
        if callable(hint):
            hint(_paris_catalogue_hint(window))
    return controls
