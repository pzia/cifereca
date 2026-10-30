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
        return identity, active, account.unique_id
    except Exception:
        # Private provider arguments, user title, credentials, signed links
        # and upstream database exceptions never reach logs or the GTK UI.
        raise ProviderConfigurationError(refused) from None



def require_local_artifact(path: Any, target_dir: Any) -> None:
    """Only accept a regular file physically inside its identity/provider root.

    ADEPT may return a nested `acsm-<digest>/book.epub`, but a provider or
    altered library tree must never register someone else's path, a symlink
    or a work/staging file outside the exact owner's identity directory.
    """
    from pathlib import Path
    try:
        if (not isinstance(path, Path) or not isinstance(target_dir, Path)
                or target_dir.is_symlink() or target_dir.parent.is_symlink()
                or not target_dir.is_dir()
                or path.is_symlink() or not path.is_file()):
            raise ValueError("not_a_local_artifact")
        root = target_dir.resolve(strict=True)
        current = path.parent
        if not path.resolve(strict=True).is_relative_to(root):
            raise ValueError("outside_identity")
        # Reject symlinks in every intermediate directory. Resolving alone
        # would accept an alias into a different identity's real tree.
        while current != target_dir:
            if current.is_symlink() or current == current.parent:
                raise ValueError("symlink_or_outside")
            current = current.parent
        if path.suffix.casefold() not in {".epub", ".pdf", ".kepub"}:
            raise ValueError("not_readable_book_format")
    except Exception:
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
