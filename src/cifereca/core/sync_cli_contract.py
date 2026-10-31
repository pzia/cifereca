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


def safe_sync_result(result: object) -> dict[str, object]:
    """Bound machine-readable status, not untrusted provider payload."""
    if not isinstance(result, dict):
        return {"status": "failed", "code": "invalid_sync_result"}
    status = result.get("status")
    if status not in ("success", "partial", "failed", "busy", "skipped", "cancelled", "complete"):
        status = "failed"
    summary: dict[str, object] = {"status": status}
    for key in ("downloaded", "completed", "scanned", "unchanged", "pending", "errors"):
        value = result.get(key)
        if type(value) is int and 0 <= value <= 1_000_000:
            summary[key] = value
    reason = result.get("reason")
    if reason in ("source_disabled", "operation_busy", "source_not_found",
                  "source_sync_partial", "cancelled"):
        summary["code"] = reason
    return summary
