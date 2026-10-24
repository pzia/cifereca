"""Private Numilog inventory provenance and owned-rights projection (#1905).

An authenticated per-account library(), not a public catalogue flag, is the
only positive ownership evidence. Neither binding checks nor projection make
network requests, resolve ACSM links, or authorize automatic acquisition.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Iterable
from typing import Any

from ..acquisition_engine import Entitlement, SourceIdentity, stable_id
from ..models import SearchResult
from .search_orchestration import _numilog_public_catalog_url

_MAX_NUMILOG_MIRROR_ROWS = 100_000


def numilog_inventory_binding_valid(service: Any, account: Any, *, session: Any) -> bool:
    """Refuse ambiguous, detached or disabled owned-store source requests.

    Check persisted connection AND in-memory account before accessing library().
    Do not fall back to current_identity or infer access from a connected
    session or public SearchResult.
    """
    try:
        if getattr(account, "provider_id", None) != "numilog":
            return False
        identity = getattr(account, "identity_id", None)
        credential = getattr(account, "credential_id", None)
        account_id = getattr(account, "unique_id", None)
        if not all(type(x) is str and x and "\x00" not in x
                   for x in (identity, credential, account_id)):
            return False
        matches = [
            item for item in service.accounts
            if getattr(item, "provider_id", None) == "numilog"
            and getattr(item, "credential_id", None) == credential
        ]
        if (len(matches) != 1
                or matches[0].identity_id != identity
                or matches[0].unique_id != account_id
                or not service.sync_source_enabled(account)
                or service.identity_by_id(identity) is None):
            return False
        if service.storage.identity_for_connection("numilog", credential) != identity:
            return False
        connections = [
            row for row in service.storage.provider_connections(identity_id=identity)
            if row.get("provider_id") == "numilog"
            and row.get("credential_id") == credential
            and row.get("enabled") is True
        ]
        if len(connections) != 1:
            return False
        # A login/reconnect can replace a session while library() is
        # retrieving pages. Never accept an old in-flight session as the
        # current authority, even if its credential and connected flag match.
        current_sessions = [
            active for active in getattr(service, "_sessions", {}).values()
            if getattr(active, "provider_id", None) == "numilog"
            and getattr(active, "credential_id", None) == credential
        ]
        if len(current_sessions) != 1 or current_sessions[0] is not session:
            return False
        return (
            getattr(session, "provider_id", None) == "numilog"
            and getattr(session, "credential_id", None) == credential
            and getattr(session, "connected", False) is True
            and getattr(getattr(session, "capabilities", None), "personal_library", False) is True
        )
    except Exception:
        # A corrupt binding is never permission for a private provider call.
        return False


def _scoped_record_id(value: Any, credential_id: str) -> str | None:
    """Accept only the provider's stable (credential, lid, variant) reference."""
    if type(value) is not str or len(value) > 512:
        return None
    scope = hashlib.sha256(credential_id.encode("utf-8")).hexdigest()[:20]
    prefix = "numilog:" + scope + ":"
    if not value.startswith(prefix):
        return None
    remainder = value[len(prefix):]
    # Never use titles/ISBNs to infer which variant/edition was purchased.
    lid, sep, variant = remainder.rpartition(":")
    if (not sep or not re.fullmatch(r"[a-zA-Z0-9_.-]{1,160}", lid)
            or not re.fullmatch(r"[0-9a-f]{16}", variant)):
        return None
    return value


def _safe_metadata_text(value: Any) -> str | None:
    if type(value) is not str or not value or len(value) > 300:
        return None
    if any(unicodedata.category(ch) in {"Cc", "Cf", "Cs"} for ch in value):
        return None
    return value


def mirror_numilog_manifest_rights(
    service: Any, rows: Iterable[SearchResult], *, identity_id: str,
    credential_id: str, account_id: str | None,
) -> int:
    """Persist positive purchases; an absent row never revokes old rights.

    Caller MUST have checked the account binding and called an authenticated
    Numilog personal library under that binding. In particular this helper is
    never called for a generic public search result.
    """
    if not identity_id or not credential_id or not account_id:
        return 0
    # Revalidate at the write boundary, too: bindings can change while
    # library() is reading pages. Neither a caller nor a stale SearchResult
    # can turn an earlier authorization into a durable purchase on its own.
    try:
        matches = [
            account for account in service.accounts
            if account.provider_id == "numilog"
            and account.credential_id == credential_id
            and account.identity_id == identity_id
            and account.unique_id == account_id
        ]
        sessions = [
            session for session in service._sessions.values()
            if getattr(session, "provider_id", None) == "numilog"
            and getattr(session, "credential_id", None) == credential_id
        ]
        if (len(matches) != 1 or len(sessions) != 1
                or not numilog_inventory_binding_valid(
                    service, matches[0], session=sessions[0],
                )):
            return 0
    except Exception:
        return 0
    # Stage at most one attested provider row per exact purchase before
    # querying SQLite. The existing implementation enumerated and decoded
    # *all* Numilog entitlements across every other account on each sync.
    staged: dict[str, SearchResult] = {}
    for row in rows:
        if not isinstance(row, SearchResult):
            continue
        if (
            row.source_provider != "numilog"
            or row.source_credential != credential_id
            or row.source_collection != "library"
            or row.scenario != "NUMILOG_LIBRARY"
            or row.owned is not True
            or row.id != row.record_id
        ):
            continue
        item_id = _scoped_record_id(row.record_id, credential_id)
        if item_id is None or item_id in staged:
            continue
        staged[item_id] = row
        if len(staged) > _MAX_NUMILOG_MIRROR_ROWS:
            # Over budget is a failure, never a false "zero owned purchases"
            # success or a partial batch of persisted rights.
            raise ValueError("numilog_rights_budget_exhausted")
    if not staged:
        return 0

    # A lazy rows generator can change/revoke the current session or account
    # while yielding pages. Its first successful binding is no authority to
    # persist later results: re-check immediately before loading/writing.
    try:
        matches = [
            account for account in service.accounts
            if account.provider_id == "numilog"
            and account.credential_id == credential_id
            and account.identity_id == identity_id
            and account.unique_id == account_id
        ]
        sessions = [
            session for session in service._sessions.values()
            if getattr(session, "provider_id", None) == "numilog"
            and getattr(session, "credential_id", None) == credential_id
        ]
        if (len(matches) != 1 or len(sessions) != 1
                or not numilog_inventory_binding_valid(
                    service, matches[0], session=sessions[0],
                )):
            return 0
    except Exception:
        return 0

    source = SourceIdentity("numilog", credential_id, account_id, identity_id)
    candidates: list[str] = []
    for item_id in staged:
        key = (identity_id, credential_id, account_id, item_id)
        candidates.append(Entitlement(source=source, item_id=item_id).entitlement_id)
        candidates.append(stable_id("numilog-owned-identity", *key))
    existing = service.storage.numilog_entitlement_reservations(
        identity_id=identity_id, credential_id=credential_id,
        account_id=account_id, candidate_ids=tuple(candidates),
    )
    by_scope = {
        (
            str(item.get("identity_id") or ""),
            str(item.get("credential_id") or ""),
            str(item.get("account_id") or ""),
            str(item.get("item_id") or ""),
        ): item
        for item in existing
        if item.get("entitlement_type") == "owned"
        and item.get("identity_id") == identity_id
        and item.get("credential_id") == credential_id
        and item.get("account_id") == account_id
    }
    reserved = {
        str(item.get("entitlement_id") or ""): (
            str(item.get("identity_id") or ""),
            str(item.get("credential_id") or ""),
            str(item.get("account_id") or ""),
            str(item.get("item_id") or ""),
        )
        for item in existing
    }
    count = 0
    for item_id, row in staged.items():
        key = (identity_id, credential_id, account_id, item_id)
        previous = by_scope.get(key)
        canonical = Entitlement(source=source, item_id=item_id).entitlement_id
        fallback = stable_id("numilog-owned-identity", *key)
        entitlement_id = (
            str(previous["entitlement_id"]) if previous else
            canonical if canonical not in reserved or reserved[canonical] == key
            else fallback
        )
        # Never overwrite a different owner's row even when its ID collides
        # with our account-specific fallback (legacy/corrupt databases).
        if entitlement_id in reserved and reserved[entitlement_id] != key:
            continue
        # Do not persist provider HTML, metadata, delivery URLs, signed
        # order/auth/rights query strings or download references.
        # A discovery-time reservation is not an atomic owner proof.
        # Only the guarded SQLite UPSERT may refresh an existing purchase.
        owned_row = {
            "entitlement_id": entitlement_id,
            "provider_id": "numilog",
            "credential_id": credential_id, "account_id": account_id,
            "identity_id": identity_id, "item_id": item_id,
            "entitlement_type": "owned", "state": "active",
            "metadata": {
                "source": "numilog.authenticated_library",
                "title": _safe_metadata_text(row.title),
                "author": _safe_metadata_text(row.author),
                "media_format": _safe_metadata_text(row.format),
                # A Numilog purchase is an ebook even when EPUB/PDF is still
                # unverified. Preserve support without inventing a format.
                "medium": "digital" if row.medium == "digital" else None,
                # Only a query-free public image URL may cross the private
                # account boundary. Never persist signed delivery links.
                "cover_url": _numilog_public_catalog_url(
                    row.thumbnail_url, cover=True,
                ),
            },
        }
        # A different process may claim the canonical ID after our
        # reservations snapshot. Recover with the same identity-scoped
        # fallback as discovery-time collisions, without ever reassigning
        # that process's right. A collision on the fallback fails closed.
        if not service.storage.entitlement_put_attested_owned(owned_row):
            if previous is not None or entitlement_id != canonical:
                continue
            owned_row["entitlement_id"] = fallback
            if not service.storage.entitlement_put_attested_owned(owned_row):
                continue
            entitlement_id = fallback
        reserved[entitlement_id] = key
        by_scope[key] = {"entitlement_id": entitlement_id}
        count += 1
    return count
