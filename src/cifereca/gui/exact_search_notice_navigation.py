"""P1 #1354: navigate to the *same* Core Search detail for a loaded notice.

Never accept a SearchResult, offer, entitlement or availability from a plugin.
Only original Core-owned, identity-scoped rows in the active Search view may
be selected. This route is read-only until the user explicitly opens it; it
does not start a new catalogue search or invent capabilities.
"""
from __future__ import annotations

from typing import Any


def _current_context(window: Any) -> tuple[str, str, str, str] | None:
    identity = getattr(getattr(window.service, "current_identity", None),
                       "identity_id", None)
    controls = getattr(window, "search_controls", None)
    if type(identity) is not str or not identity or controls is None:
        return None
    state = controls.state()
    return (state.text, state.field, state.scope, identity)


def loaded_notice_key(
    window: Any, *, provider: str, credential: str, record_id: str,
    docbase: str, source_collection: str, identity_id: str,
) -> str | None:
    """Return only an unambiguous, visible row key, never an offer/rights."""
    if getattr(window, "_closed", False) or getattr(window, "_search_mode", "") != "global":
        return None
    fields = (provider, credential, record_id, docbase, source_collection, identity_id)
    if any(type(value) is not str or not 1 <= len(value) <= 512
           for value in fields):
        return None
    context = _current_context(window)
    if (context is None or context[-1] != identity_id
            or getattr(window, "_loaded_search_context", None) != context):
        return None
    try:
        # A provider disabled since loading a page cannot be selected as if
        # its capabilities remained available.
        active = {name for name, _label in window.service.searchable_providers()
                  if type(name) is str}
        if provider not in active:
            return None
    except Exception:
        return None
    loaded = getattr(window, "_remote_search_results", ())
    if type(loaded) not in (tuple, list) or len(loaded) > 10000:
        return None
    matches = [
        item for item in loaded
        if (getattr(item, "source_provider", None),
            getattr(item, "source_credential", None),
            getattr(item, "record_id", None),
            getattr(item, "docbase", None),
            getattr(item, "source_collection", None) or "") ==
        (provider, credential, record_id, docbase, source_collection)
    ]
    if len(matches) != 1:
        return None
    item = matches[0]
    if (getattr(item, "metadata", None) or {}).get("search_provenance") == "known_catalog_stale":
        return None
    item_id = getattr(item, "id", None)
    if type(item_id) is not str or not item_id:
        return None
    key = f"{provider}:{item_id}"
    if getattr(window, "search_objects", {}).get(key) is not item:
        return None
    page = getattr(window, "search_page", None)
    store = getattr(page, "store", None)
    if store is None or not callable(getattr(page, "select_key", None)):
        return None
    # The row must be visible under current Search filters, not merely hidden
    # in an old remote result buffer. A selected provider/edition remains exact.
    if sum(getattr(store.get_item(i), "key", None) == key
           for i in range(store.get_n_items())) != 1:
        return None
    return key


def can_open_loaded_search_notice(window: Any, **coordinates: str) -> bool:
    return loaded_notice_key(window, **coordinates) is not None


def open_loaded_search_notice(window: Any, **coordinates: str) -> bool:
    key = loaded_notice_key(window, **coordinates)
    if key is None:
        return False
    # Both calls are ordinary Search navigation; select_key triggers the
    # SAME existing DetailPanel/holdings/action controller as a Search click.
    # No book detail or entitlement from the plugin crosses this boundary.
    window.navigate("search")
    if (getattr(window, "_closed", False)
            or loaded_notice_key(window, **coordinates) != key):
        return False
    return bool(window.search_page.select_key(key))
