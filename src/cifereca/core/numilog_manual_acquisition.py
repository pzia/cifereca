"""Fail-closed admission for one *explicit* Numilog manual acquisition (#1905).

No public search result, catalogue flag, selected GTK identity, or cached
download URL authorizes a purchase. The real provider must still fetch an
exact, freshly owned library row before it follows any delivery URL.
"""
from __future__ import annotations

from typing import Any

from ..models import SearchResult
from ..providers import ProviderConfigurationError
from .numilog_manifest_rights import _scoped_record_id, numilog_inventory_binding_valid


def require_source(service: Any, item: Any, *, session: Any = None) -> tuple[Any, Any]:
    """Recheck provider, source and live session before/after the download."""
    refused = "Numilog : achat ou compte non attesté ; actualiser mes achats"
    try:
        if (
            type(item) is not SearchResult
            or item.source_provider != "numilog"
            or type(item.source_credential) is not str
            or not item.source_credential
            or item.source_collection != "library"
            or item.scenario != "NUMILOG_LIBRARY"
            or item.owned is not True
            or item.downloadable is not True
            or type(item.record_id) is not str
            or item.id != item.record_id
            or _scoped_record_id(item.record_id, item.source_credential) is None
            or (type(item.metadata) is dict
                and item.metadata.get("search_provenance") == "known_catalog_stale")
        ):
            raise ValueError("invalid_item")
        accounts = [
            account for account in service.accounts
            if account.provider_id == "numilog"
            and account.credential_id == item.source_credential
        ]
        if len(accounts) != 1:
            raise ValueError("ambiguous_account")
        account = accounts[0]
        active = service._sessions.get(
            service._session_key("numilog", item.source_credential)
        )
        if (active is None
                or (session is not None and active is not session)
                or not numilog_inventory_binding_valid(
                    service, account, session=active,
                )
                or getattr(getattr(active, "capabilities", None),
                           "direct_download", False) is not True):
            raise ValueError("source_rebound")
        identity = service.identity_by_id(account.identity_id)
        if identity is None:
            raise ValueError("unknown_identity")
        return identity, active
    except Exception:
        # Private provider arguments, user title, credentials, signed links
        # and upstream database exceptions never reach logs or the GTK UI.
        raise ProviderConfigurationError(refused) from None
