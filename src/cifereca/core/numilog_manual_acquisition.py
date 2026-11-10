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
from .passive_source_session import passive_direct_download_capable


def _canonical_guarded_source(session: Any) -> bool:
    """Provider-neutral, passive v2 acceptance; never demands legacy direct_download."""
    from inspect import getattr_static
    from .canonical_delivery_abi import has_complete_delivery_abi
    return (has_complete_delivery_abi(session)
            and callable(getattr_static(
                session, "downloadable_selected_delivery_candidate_guarded", None,
            )))


def require_source(service: Any, item: Any, *, session: Any = None,
                   canonical_only: bool = False) -> tuple[Any, Any, str]:
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
                or not (
                    canonical_only is True
                    and _canonical_guarded_source(active)
                    or canonical_only is False
                    and passive_direct_download_capable(active)
                )):
            raise ValueError("source_rebound")
        identity = service.identity_by_id(account.identity_id)
        if identity is None:
            raise ValueError("unknown_identity")
        return identity, active, account.unique_id
    except Exception:
        # Private provider arguments, user title, credentials, signed links
        # and upstream database exceptions never reach logs or the GTK UI.
        raise ProviderConfigurationError(refused) from None



def require_local_artifact(path: Any, target_dir: Any) -> None:
    """Preserve the Numilog refusal code; the physical check is Core-neutral."""
    from .local_received_artifact import received_book_file_within_root
    if not received_book_file_within_root(path, target_dir):
        raise ProviderConfigurationError(
            "Numilog : artefact local non attesté ; rapprochement requis"
        ) from None


def admission_scope(item: SearchResult) -> tuple:
    """Snapshot only the dispatch coordinates; never retain mutable metadata."""
    return tuple(getattr(item, name, None) for name in (
        "source_provider", "source_credential", "id", "record_id",
        "source_collection", "scenario", "revision_id", "format", "content_type",
    ))


def require_admission(service, item, admission, *, pinned_scope, session,
                      identity_id, account_id):
    """Replay an optional caller's rights check without permitting retargeting.

    Called exclusively under the acquisition lock. The provider still resolves
    its exact fresh authenticated purchase row before using any delivery link.
    This callback alone is not an entitlement or automatic-backup consent.
    """
    if admission is None:
        return item
    from ..plugin_api import Cancelled
    try:
        candidate = admission()
        if type(candidate) is not SearchResult or admission_scope(candidate) != pinned_scope:
            raise ValueError("changed_purchase")
        owner, _session, account = require_source(service, candidate, session=session)
        if owner.identity_id != identity_id or account != account_id:
            raise ValueError("changed_owner")
        return candidate
    except Cancelled:
        raise
    except Exception:
        raise ProviderConfigurationError(
            "Numilog : autorisation ciblée modifiée ou indisponible"
        ) from None
