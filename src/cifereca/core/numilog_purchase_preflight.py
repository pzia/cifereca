"""Scoped, no-network Numilog purchase preflight (P0 #1905).

A cached ownership right is NOT a current entitlement to download. This
read-only broker operation cannot produce signed delivery links, file paths,
acquisition tokens, provider payloads, or any authorization to acquire. A
future executor must re-fetch the authenticated purchase under its *own*
source-scoped lock and explicit, independent materialization consent.
"""
from __future__ import annotations

from typing import Any

from ..plugin_api import SyncResult
from .numilog_local_preview import _Binding, _item_id, _valid_binding
from .numilog_manifest_rights import numilog_inventory_binding_valid


def _response(code: str, *, valid_candidate: bool = False) -> SyncResult:
    return SyncResult(
        ok=valid_candidate, changed=0,
        warnings=() if valid_candidate else (code,),
        facts={
            "phase": "materialize", "status": code,
            "read_only": True, "offline": True,
            "acquisition_authorized": False,
            "confirmation_available": False,
            "schedule_available": False,
            "manifest_refreshed": False,
            "requires_manifest_validation": True,
            "candidate_unverified": valid_candidate,
        },
    )


def preflight(broker: Any, account: Any, source_id: str, item_id: Any) -> SyncResult:
    """Inspect just one identity/credential/account/item/variant in SQLite."""
    account = _Binding(
        getattr(account, "provider_id", None),
        getattr(account, "identity_id", None),
        getattr(account, "credential_id", None),
        getattr(account, "unique_id", None),
    )
    if not _valid_binding(broker, account, source_id):
        return _response("invalid_source")
    if not _item_id(item_id, account.credential_id):
        return _response("invalid_item_id")
    service = broker.service

    def connected_session() -> Any | None:
        try:
            session = service._sessions.get(
                service._session_key("numilog", account.credential_id),
            )
            if (
                session is not None
                and numilog_inventory_binding_valid(
                    service, account, session=session,
                )
                and getattr(
                    getattr(session, "capabilities", None),
                    "direct_download", False,
                ) is True
            ):
                return session
        except Exception:
            pass
        return None

    session = connected_session()
    if session is None:
        return _response("source_not_ready")
    try:
        evidence = service.storage.numilog_purchase_preflight_evidence(
            identity_id=account.identity_id,
            credential_id=account.credential_id,
            account_id=account.unique_id, item_id=item_id,
        )
    except Exception:
        return _response("evidence_unavailable")

    # A reconnect, disable or identity rebound while reading SQLite must
    # invalidate the snapshot. Comparison by identity of the live session
    # prevents accepting a new connection merely sharing credential text.
    if (not _valid_binding(broker, account, source_id)
            or connected_session() is not session):
        return _response("source_changed")
    rights = evidence.get("entitlements")
    ledger = evidence.get("acquisitions")
    placement_conflict = evidence.get("placement_conflict")
    if (type(rights) is not list or type(ledger) is not list
            or type(placement_conflict) is not bool):
        # A broken/older evidence projection cannot be used to authorize
        # an attempt without checking the same placement guard as the
        # locked executor. No private path/owner leaves this boundary.
        return _response("evidence_unavailable")
    if len(rights) > 1 or len(ledger) > 2:
        return _response("needs_reconciliation")
    if len(rights) != 1:
        return _response("purchase_unproven")
    right = rights[0]
    metadata = right.get("metadata") if type(right) is dict else None
    if (
        type(metadata) is not dict
        or metadata.get("source") != "numilog.authenticated_library"
        or right.get("identity_id") != account.identity_id
        or right.get("credential_id") != account.credential_id
        or right.get("account_id") != account.unique_id
        or right.get("item_id") != item_id
        or right.get("entitlement_type") != "owned"
        or right.get("state") != "active"
        or right.get("loan_id") is not None
        or right.get("expires_at") is not None
        or type(right.get("entitlement_id")) is not str
        or not right["entitlement_id"]
    ):
        return _response("purchase_unproven")
    if ledger or placement_conflict:
        # The locked executor rejects either prior acquisitions OR an existing
        # Numilog placement. Both require offline reconciliation, never a
        # new signed URL / duplicate file / misleading "acquirable" preview.
        return _response("needs_reconciliation")
    if metadata.get("downloadable") is False:
        return _response("not_downloadable")
    return _response("candidate_unverified", valid_candidate=True)
