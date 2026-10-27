"""P0 #1354 — navigate from a recommendation to an EXACT cached Search notice.

The plugin supplies only source coordinates. Core reads one version-2 mirrored
SQLite row using a deterministic key and renders it through the existing
Search detail. The first step NEVER requests holdings, borrows, downloads,
refreshes a provider or grants rights. The user may separately request live
holdings through the normal Search action and its existing admission guards.
"""
from __future__ import annotations

from inspect import getattr_static
from typing import Any

from ..core.known_catalog_provenance import known_catalog_search_result
from ..core.search_orchestration import mirrored_catalogue_scoped_id
from .query_model import QueryState


def _exact_known_row(window: Any, *, provider: str, credential: str,
                     record_id: str, docbase: str, source_collection: str,
                     identity_id: str) -> dict | None:
    """Fail closed: no cache scan, provider getter, fallback account or I/O."""
    if getattr(window, "_closed", False):
        return None
    if type(source_collection) is not str:
        return None
    scope_id = mirrored_catalogue_scoped_id(
        identity_id, provider, record_id, credential, docbase, source_collection,
    )
    if scope_id is None:
        return None
    service = getattr(window, "service", None)
    identity = getattr(getattr(service, "current_identity", None), "identity_id", None)
    if identity != identity_id:
        return None
    try:
        available = {
            name for name, _label in service.searchable_providers()
            if type(name) is str
        }
        if provider not in available:
            return None
        # A retired account B must never borrow A's registered session. The
        # static data check cannot execute plugin properties or network calls.
        session = service._sessions.get(service._session_key(provider, credential))
        if (session is None
                or getattr_static(session, "provider_id", None) != provider
                or getattr_static(session, "credential_id", None) != credential
                or getattr_static(session, "connected", None) is not True
                or getattr_static(session, "_closed", False) is True):
            return None
        if service.storage.identity_for_connection(provider, credential) != identity_id:
            return None
        # The connection may be explicitly disabled while a stale Python
        # session remains registered. Only ONE enabled current binding can
        # permit a live-availability action from the cached Search detail.
        bindings = service.storage.provider_connections(identity_id=identity_id)
        if (type(bindings) is not list or len(bindings) > 512
                or sum(
                    type(binding) is dict
                    and binding.get("provider_id") == provider
                    and binding.get("credential_id") == credential
                    and binding.get("enabled") is True
                    for binding in bindings
                ) != 1):
            return None
        entry = service.storage.catalog_entries_by_ids([scope_id]).get(scope_id)
    except Exception:
        return None
    if (type(entry) is not dict
            or (entry.get("identity_id"), entry.get("provider_id"),
                entry.get("item_id")) != (identity_id, provider, record_id)):
        return None
    meta = entry.get("metadata")
    if (type(meta) is not dict
            or meta.get("catalogue_scope_version") != 2
            or meta.get("source_credential") != credential
            or meta.get("docbase") != docbase
            or (meta.get("source_collection") or "") != source_collection
            or meta.get("search_provenance") == "known_catalog_stale"):
        return None
    row = {
        "key": "known:" + scope_id,
        "title": entry.get("title"),
        "author": entry.get("author"),
        "provider_id": provider,
        "identity_id": identity_id,
        "item_id": record_id,
        # Fresh availability/ownership must be read from the provider after
        # explicit user activation. A cached state is not a loan grant.
        "state": "remote_catalog",
        "local": False,
        "media_format": entry.get("media_format"),
        "medium": meta.get("medium"),
        "source": "known_catalog",
        "thumbnail_url": meta.get("cover_url") or meta.get("thumbnail_url"),
        "metadata": meta,
    }
    if known_catalog_search_result(row, identity_id=identity_id) is None:
        return None
    return row


def can_open_known_search_notice(window: Any, **coordinates: str) -> bool:
    return _exact_known_row(window, **coordinates) is not None


def open_known_search_notice(window: Any, **coordinates: str) -> bool:
    row = _exact_known_row(window, **coordinates)
    if row is None:
        return False
    try:
        # Navigation may change identity or disconnect a source. Re-read that
        # exact SQLite ID and its current session BEFORE showing any action.
        window.navigate("search")
        current = _exact_known_row(window, **coordinates)
        if current is None or current != row:
            return False
        title = current["title"]
        if type(title) is not str or not title.strip():
            return False
        provider = coordinates["provider"]
        window._search_generation += 1
        window._search_detail_request_generation += 1
        window._last_search_query = title
        window.search_controls.set_state(
            QueryState(text=title, field="title", scope="all",
                       providers=(provider,)),
            notify=False,
        )
        # Only the one independently attested mirror row enters Search.
        # This does not call run_search, holdings or a plugin action.
        window._populate_local_search([current])
        return window.search_page.select_key(current["key"]) is True
    except Exception:
        # A failed navigation is not permission to try an alternate edition.
        return False
