"""Provider-neutral CLI source selection. No network, filesystem or consent mutation."""
from __future__ import annotations

from typing import Any


class SyncSelectionError(ValueError):
    """Closed, machine-readable selection failure."""


def select_sync_accounts(
    accounts: list[Any],
    *,
    provider: str = "",
    identity: str = "",
    credential: str = "",
    account_id: str | None = None,
) -> list[Any]:
    """Filter exact account coordinates, rejecting ambiguous or malformed bindings.

    An account ID may be the empty upstream primary identifier. The caller
    must never infer a credential or profile from provider name alone.
    """
    for value in (provider, identity, credential):
        if type(value) is not str or any(c in value for c in "\x00\r\n"):
            raise SyncSelectionError("invalid_source_selector")
    if account_id is not None and (type(account_id) is not str
                                   or any(c in account_id for c in "\x00\r\n")):
        raise SyncSelectionError("invalid_source_selector")
    selected = []
    seen = set()
    for item in accounts:
        coords = (
            getattr(item, "identity_id", None),
            getattr(item, "provider_id", None),
            getattr(item, "credential_id", None),
            getattr(item, "unique_id", None),
        )
        if not all(type(x) is str for x in coords):
            continue
        if not coords[0] or not coords[1] or not coords[2]:
            continue
        if ((provider and coords[1] != provider)
                or (identity and coords[0] != identity)
                or (credential and coords[2] != credential)
                or (account_id is not None and coords[3] != account_id)):
            continue
        if coords in seen:
            raise SyncSelectionError("ambiguous_source_binding")
        seen.add(coords)
        selected.append(item)
    return selected


def require_exact_sync_account(accounts: list[Any], **selectors: str) -> Any:
    """A destructive/network run must identify exactly one account."""
    if (not all(selectors.get(k) for k in ("provider", "identity", "credential"))
            or selectors.get("account_id") is None):
        raise SyncSelectionError("source_scope_required")
    selected = select_sync_accounts(accounts, **selectors)
    if not selected:
        raise SyncSelectionError("source_not_found")
    if len(selected) != 1:
        raise SyncSelectionError("ambiguous_source_selection")
    return selected[0]



def require_unique_sync_account(accounts: list[Any], **selectors: str) -> Any:
    """Resolve exactly one persisted source with provider and identity required.

    No missing credential, provider or upstream account ID is guessed if
    multiple bindings match. This operation must run BEFORE connection/network.
    """
    if not selectors.get("identity") or not selectors.get("provider"):
        raise SyncSelectionError("source_scope_required")
    try:
        selected = select_sync_accounts(accounts, **selectors)
    except SyncSelectionError as exc:
        if str(exc) == "ambiguous_source_binding":
            raise SyncSelectionError("account_ambiguous") from None
        raise
    if not selected:
        raise SyncSelectionError("source_not_configured")
    if len(selected) != 1:
        raise SyncSelectionError("account_ambiguous")
    return selected[0]

# Existing Core source-sync and A→B envelopes; values stay closed and bounded.
_SYNC_CODES = frozenset({
    "source_disabled", "operation_busy", "source_not_found",
    "source_sync_partial", "source_sync_failed", "source_sync_cancelled",
    "account_ambiguous", "cancelled", "sync_unavailable",
    "invalid_sync_result",
})
_RIGHTS_STATES = frozenset({"success", "partial", "failed", "schema_unavailable"})
_RIGHTS_COUNTS = (
    "observed", "attested_active", "source_references_changed",
    "present_attested", "pending", "skipped_archived",
)


def safe_sync_result(result: object) -> dict[str, object]:
    """Project only known Core statuses, A observation and A→B counts."""
    if type(result) is not dict:
        return {"status": "failed", "code": "invalid_sync_result"}
    status = result.get("status")
    if type(status) is not str or status not in (
        "success", "partial", "failed", "busy", "skipped", "cancelled", "complete"
    ):
        status = "failed"
    summary: dict[str, object] = {"status": status}
    for key in (
        "downloaded", "completed", "scanned", "unchanged", "pending",
        "errors", "loans", "library", "files_materialized",
    ):
        value = result.get(key)
        if type(value) is int and 0 <= value <= 1_000_000:
            summary[key] = value
    reason = result.get("code", result.get("reason"))
    if type(reason) is str and reason in _SYNC_CODES:
        summary["code"] = reason

    rights = result.get("canonical_rights")
    if type(rights) is dict:
        projected: dict[str, object] = {}
        if type(rights.get("status")) is str and rights["status"] in _RIGHTS_STATES:
            projected["status"] = rights["status"]
        for key in _RIGHTS_COUNTS:
            value = rights.get(key)
            if type(value) is int and 0 <= value <= 1_000_000:
                projected[key] = value
        if projected:
            summary["canonical_rights"] = projected

    execution = result.get("canonical_execution")
    if type(execution) is dict:
        from .diagnostic_source_sync import CANONICAL_EXECUTION_STATUSES
        if (set(execution) == set(CANONICAL_EXECUTION_STATUSES)
                and all(type(execution[key]) is int and 0 <= execution[key] <= 1_000_000
                        for key in CANONICAL_EXECUTION_STATUSES)):
            summary["canonical_execution"] = {
                key: execution[key] for key in CANONICAL_EXECUTION_STATUSES
            }
    return summary
