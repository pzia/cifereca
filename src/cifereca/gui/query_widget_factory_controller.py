"""CoreUI #1129: compose search controls from public provider capabilities.

The owning MainWindow retains the services, plugin manager and widgets.
The fallback GTK QueryControls class is injected to keep this controller
importable without PyGObject and to preserve the optional widget service.
"""
from __future__ import annotations


def build_query_controls(window, changed, activate, *, library=False, QueryControls):
    """Resolve one widget factory, preserving current provider ordering and fallback."""
    providers = dict(window.service.searchable_providers())
    providers["local"] = "Local"
    for row in window.service.catalog_entries():
        provider = str(row.get("provider_id") or "local")
        providers.setdefault(provider, window.service.provider_display_name(provider))
    providers = sorted(providers.items(), key=lambda pair: pair[1])
    catalog = window.plugin_manager.widget_catalog() if window.plugin_manager else None
    factory = getattr(catalog, "build_query_controls", None)
    controls = None
    if callable(factory):
        try:
            controls = factory(providers, changed, activate, library=library)
        except Exception as exc:
            window.diagnostics.add(
                "PLUGIN", "query widget", "error", detail={"error": str(exc)},
            )
    if controls is None:
        controls = QueryControls(providers, changed, activate, library=library)
    setter = getattr(controls, "set_provider_search_capabilities", None)
    if callable(setter) and not library:
        setter(window.service.provider_search_capabilities())
    return controls
