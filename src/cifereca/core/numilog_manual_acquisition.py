"""Fail-closed admission for one *explicit* Numilog manual acquisition (#1905).

No public search result, catalogue flag, selected GTK identity, or cached
download URL authorizes a purchase. The real provider must still fetch an
exact, freshly owned library row before it follows any delivery URL.
"""
from __future__ import annotations

from typing import Any

from ..models import SearchResult
from ..providers import ProviderConfigurationError
from .numilog_manifest_rights import numilog_inventory_binding_valid
from .credential_scoped_record import exact_credential_scoped_record


def _canonical_guarded_source(session: Any) -> bool:
    """Provider-neutral, passive v2 acceptance; never demands legacy direct_download."""
    from inspect import getattr_static
    from .canonical_delivery_abi import has_complete_delivery_abi
    return (has_complete_delivery_abi(session)
            and callable(getattr_static(
                session, "downloadable_selected_delivery_candidate_guarded", None,
            )))


def require_source(service: Any, item: Any, *, session: Any = None) -> tuple[Any, Any, str]:
    """Recheck provider, source and live session before/after the download."""
    refused = "Numilog : offre téléchargeable ou compte non attesté ; actualiser le fournisseur"
    try:
        if (
            type(item) is not SearchResult
            or item.source_provider != "numilog"
            or type(item.source_credential) is not str
            or not item.source_credential
            or item.source_collection != "library"
            or item.scenario != "NUMILOG_LIBRARY"
            # Download capability comes from the exact authenticated library
            # observation; an optional cached ownership label is not a veto.
            or item.downloadable is not True
            or type(item.record_id) is not str
            or item.id != item.record_id
            or exact_credential_scoped_record(
                item.record_id, provider_id="numilog", credential_id=item.source_credential,
            ) is None
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
                or not _canonical_guarded_source(active)):
            raise ValueError("source_rebound")
        identity = service.identity_by_id(account.identity_id)
        if identity is None:
            raise ValueError("unknown_identity")
        return identity, active, account.unique_id
    except Exception:
        # Private provider arguments, user title, credentials, signed links
        # and upstream database exceptions never reach logs or the GTK UI.
        raise ProviderConfigurationError(refused) from None



