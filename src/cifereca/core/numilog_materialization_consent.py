"""Numilog startup *materialization* consent, distinct from inventory (#1905).

No legacy backup switch, cache hit, schedule for another store, selected GUI
identity or source-sync grants permission.  A two-step explicit confirmation is
required for each exact source; old/malformed preferences default to manual.
"""
from __future__ import annotations

from typing import Any
from ..plugin_api import SyncResult
from .numilog_local_preview import _Binding, _identity_root, _valid_binding
from .numilog_manifest_rights import numilog_inventory_binding_valid

_PREFIX = "sync.source.numilog.materialization.consent.v1."
_ACTIVE = "on_start:v1"
_MANUAL = "manual"
_MAX_ITEMS_PER_OPENING = 8
_MIN_SECONDS_BETWEEN_STARTS = 3600


def policy_key(source_id: str) -> str:
    return _PREFIX + source_id


def _binding(broker: Any, source_id: str):
    rows = [
        row for row in broker.service.accounts
        if getattr(row, "provider_id", None) == "numilog"
        and broker._source_id(row) == source_id
    ]
    if len(rows) != 1:
        return None
    account = rows[0]
    scoped = _Binding(
        account.provider_id, account.identity_id,
        account.credential_id, account.unique_id,
    )
    if not _valid_binding(broker, scoped, source_id):
        return None
    session = broker.service._sessions.get(
        broker.service._session_key("numilog", account.credential_id),
    )
    if (
        session is None
        or not numilog_inventory_binding_valid(
            broker.service, account, session=session,
        )
        or getattr(getattr(session, "capabilities", None),
                   "direct_download", None) is not True
    ):
        return None
    roots = _identity_root(broker.service, account.identity_id)
    if roots is None or len(roots) != 1:
        return None
    return account, session, roots


def mode(broker: Any, source_id: str) -> str:
    try:
        return ("on_start" if broker.service.storage.metadata_get(
            policy_key(source_id),
        ) == _ACTIVE and _binding(broker, source_id) is not None else "manual")
    except Exception:
        return "manual"


def _receipt(status: str, *, ok: bool = False, **facts: Any) -> SyncResult:
    return SyncResult(
        ok=ok, changed=0, warnings=() if ok else (status,),
        facts={"phase": "materialize", "status": status,
               "acquisition_authorized": False, **facts},
    )


def prepare(broker: Any, source_id: str) -> SyncResult:
    """An explicit user click: local SHA preview and an expiring one-use token."""
    try:
        bound = _binding(broker, source_id)
        if bound is None:
            return _receipt("source_not_ready")
        account, session, roots = bound
        preview = broker.preview_local_materialization(
            source_id, verify_local_files=True,
        )
        fields = preview.facts
        if (preview.ok is not True
                or fields.get("status") != "preview_only"
                or fields.get("acquisition_authorized") is not False
                or fields.get("manifest_refreshed") is not False
                or fields.get("files_verified") is not True
                or type(fields.get("counts")) is not dict):
            return _receipt("preview_unavailable")
        counts = fields["counts"]
        if (set(counts) != {
            "owned_purchases", "already_local_linked", "already_local_verified",
            "needs_file_verification", "potentially_missing",
            "candidate_unverified", "needs_reconciliation",
            "attention_required", "not_downloadable",
            "ignored_unattested_rights",
        } or any(type(value) is not int or not 0 <= value <= 100_000
                 for value in counts.values())):
            return _receipt("preview_unavailable")
        # Exact session and library configuration are kept only in memory.
        scope = (source_id, account.identity_id, account.credential_id,
                 account.unique_id)
        snapshot = (id(session), tuple(str(root) for root in roots),
                    tuple(sorted(counts.items())))
        token = broker._numilog_consent_plans.put(scope, snapshot, ())
        return _receipt(
            "confirmation_required", ok=True,
            confirmation_token=token, expires_in_seconds=600,
            requires_explicit_confirmation=True,
            manifest_refreshed=False, files_verified=True,
            counts=dict(counts), schedule_available=True,
        )
    except Exception:
        return _receipt("preview_unavailable")


def confirm(broker: Any, source_id: str, token: str, *, mode: str) -> SyncResult:
    """Opt in only after consuming a matching proposal, never via old flags."""
    if type(mode) is not str or mode != "on_start":
        return _receipt("invalid_mode")
    try:
        bound = _binding(broker, source_id)
        if bound is None:
            return _receipt("source_not_ready")
        account, session, roots = bound
        scope = (source_id, account.identity_id, account.credential_id,
                 account.unique_id)
        row = broker._numilog_consent_plans.take(token, scope)
        preview = broker.preview_local_materialization(
            source_id, verify_local_files=True,
        )
        counts = preview.facts.get("counts")
        evidence = (id(session), tuple(str(root) for root in roots),
                    tuple(sorted(counts.items()))) if type(counts) is dict else None
        if (preview.ok is not True
                or preview.facts.get("files_verified") is not True
                or evidence != row[2] or _binding(broker, source_id) is None):
            return _receipt("preview_changed")
        broker.service.storage.metadata_set(policy_key(source_id), _ACTIVE)
        broker._notify()
        return _receipt("on_start", ok=True, mode="on_start",
                        next_trigger="next_opening", schedule_available=True)
    except Exception:
        return _receipt("confirmation_unavailable")


def revoke(broker: Any, source_id: str) -> str:
    # Revocation must work even after a session disconnects or a source is
    # disabled. A source unknown to this installation cannot be fabricated.
    rows = [
        row for row in broker.service.accounts
        if getattr(row, "provider_id", None) == "numilog"
        and broker._source_id(row) == source_id
    ]
    if len(rows) != 1:
        raise ValueError("unknown_numilog_source")
    broker.service.storage.metadata_set(policy_key(source_id), _MANUAL)
    with broker._numilog_consent_plans.lock:
        broker._numilog_consent_plans.entries.clear()
    broker._notify()
    return "manual"
