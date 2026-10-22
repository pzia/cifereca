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


def _paris_search_notice(window):
    """Read-only, identity-scoped hint; do not offer a disconnected provider as selectable."""
    service = window.service
    identity = getattr(getattr(service, "current_identity", None), "identity_id", None)
    reader = getattr(service, "catalogue_source_capabilities", None)
    if type(identity) is not str or not identity or not callable(reader):
        return ""
    try:
        inventory = reader(identity_id=identity)
        matching = [
            item for item in inventory
            if getattr(item, "provider_id", None) == "paris"
            and getattr(item, "identity_scope", None) == "authenticated"
            and getattr(item, "catalog_search_supported", None) is True
        ]
        if not matching or getattr(matching[0], "catalog_search_available", None) is True:
            return ""
        # service.accounts_for_identity contains *connected* accounts only.
        # A failed login removes those rows: it cannot prove the absence of
        # configured credentials. Consult the identity-scoped config projection.
        config = getattr(service, "config", None)
        config_reader = getattr(config, "provider_accounts", None)
        if not callable(config_reader):
            return "Bibliothèques de Paris : source non connectée ; état du compte indéterminé."
        rows = config_reader("paris")
        if type(rows) not in (list, tuple) or len(rows) > 128:
            return "Bibliothèques de Paris : source non connectée ; état du compte indéterminé."
        own = [
            row for row in rows
            if type(row) is dict and row.get("identity_id") == identity
        ]
    except Exception:
        # Never leak provider error text, credentials or another identity's state.
        return "Bibliothèques de Paris : source non connectée ; état du compte indéterminé."
    if any(row.get("enabled", True) is True for row in own):
        return "Bibliothèques de Paris : compte configuré mais source non connectée ; recherche en ligne indisponible."
    if own:
        return "Bibliothèques de Paris : compte désactivé pour ce profil ; recherche en ligne indisponible."
    if any(type(row) is dict and not row.get("identity_id") for row in rows):
        return "Bibliothèques de Paris : source non connectée ; profil du compte à vérifier."
    return "Bibliothèques de Paris : aucun compte rattaché à ce profil ; recherche en ligne indisponible."


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
                    notice(_paris_search_notice(window))
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
        notice(_paris_search_notice(window))
    return controls
