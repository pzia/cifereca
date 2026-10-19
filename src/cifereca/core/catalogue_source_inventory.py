"""Read-only separation of public catalogue search and personal inventories (#2385).

This is *not* a catalogue sync engine. An active provider may offer searching
without implementing any bounded snapshot/delta/catalogue-refresh operation.
Never infer a refresh capability from search pagination, connected accounts,
existing cache rows or direct-download/holdings flags.
"""
from __future__ import annotations

from typing import Any

from ..plugin_api.models import CatalogueSourceCapability
from .public_provider_diagnostics import registered_public_provider_id, UNKNOWN_PROVIDER


def catalogue_source_capabilities(
    service: Any, *, identity_id: str | None = None,
) -> tuple[CatalogueSourceCapability, ...]:
    """Snapshot Core's *registered, enabled* provider capabilities with no I/O.

    Legacy SynchronizationBroker.sources() remains exclusively account/rights
    based. No pseudo Account is ever synthesized for public catalogues here.
    """
    if identity_id is None:
        identity = getattr(service, "current_identity", None)
        identity_id = getattr(identity, "identity_id", None)
    if type(identity_id) is not str or not identity_id:
        return ()
    # An arbitrary provided identity must never project another person's
    # account readiness. Only the active or explicitly registered identity.
    identities = getattr(service, "identities", ())
    known = {getattr(row, "identity_id", None) for row in identities}
    if identity_id not in known:
        return ()

    registry = service.registry
    sessions = tuple(getattr(service, "_sessions", {}).values())
    enabled = getattr(service, "_provider_enabled_for_identity", None)
    accounts_for_identity = getattr(service, "accounts_for_identity", None)
    # Metadata-only: accounts_for_identity is the existing in-memory
    # projection. Do not call it unless an authenticated provider needs it.
    accounts: tuple[Any, ...] | None = None
    result: list[CatalogueSourceCapability] = []
    for plugin in registry.all():
        descriptor = plugin.descriptor
        provider_id = registered_public_provider_id(registry, getattr(descriptor, "id", None))
        if provider_id == UNKNOWN_PROVIDER or getattr(descriptor, "state", None) != "ready":
            continue
        if not callable(enabled) or not enabled(provider_id, identity_id):
            continue
        caps = getattr(descriptor, "capabilities", None)
        search = getattr(caps, "search", None) is True
        personal = (getattr(caps, "personal_library", None) is True
                    or getattr(caps, "loans", None) is True)
        if not (search or personal):
            continue  # technical / transform-only plugins are not catalogues
        authenticated = bool(getattr(descriptor, "credential_fields", ()))
        if authenticated:
            if accounts is None:
                accounts = tuple(accounts_for_identity(identity_id)) if callable(accounts_for_identity) else ()
            matching = {
                getattr(account, "credential_id", None) for account in accounts
                if getattr(account, "identity_id", None) == identity_id
                and getattr(account, "provider_id", None) == provider_id
            }
            connected = any(
                getattr(session, "provider_id", None) == provider_id
                and getattr(session, "credential_id", None) in matching
                and getattr(session, "connected", None) is True
                for session in sessions
            )
        else:
            connected = any(
                getattr(session, "provider_id", None) == provider_id
                and getattr(session, "connected", None) is True
                for session in sessions
            )
        result.append(CatalogueSourceCapability(
            provider_id=provider_id,
            identity_scope="authenticated" if authenticated else "public",
            catalog_search_supported=search,
            catalog_search_available=search and connected,
            # Never infer an opt-in refresh or snapshot from search, OPDS,
            # local known-cache indexing or a personal library sync.
            catalog_refresh="unsupported",
            personal_inventory_supported=personal,
            personal_inventory_available=personal and connected and authenticated,
        ))
    result.sort(key=lambda row: row.provider_id)
    return tuple(result)


__all__ = ["CatalogueSourceCapability", "catalogue_source_capabilities"]
