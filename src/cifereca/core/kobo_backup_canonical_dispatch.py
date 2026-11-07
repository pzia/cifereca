"""#4858 W2: route a selected legacy Kobo resume through canonical A→B.

This adapter exists only at the PUBLIC, *unlocked* LibraryService boundary.
The 0.7 backup worker still owns incomplete-ABI sessions; its internal entry
points remain closed to complete-ABI sessions. A canonical Sync owns the global
lock itself, validates fresh provider A/offers/consent and publishes B.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..models import Account
from ..plugin_api import Cancelled
from ..providers import ProviderConfigurationError
from .canonical_delivery_abi import has_complete_delivery_abi
from .source_context import source_context_epoch
from .source_sync_consent import read_policy, scope_for_account

_COUNTS = frozenset({
    "downloaded", "recovered", "present_attested", "pending", "blocked",
    "failed", "cancelled", "skipped_archived", "inactive",
})


class _Cancel:
    def __init__(self, callback):
        self.callback = callback

    def check_cancelled(self):
        if self.callback is None:
            return
        try:
            if self.callback() is True:
                raise Cancelled("canonical_sync_cancelled")
        except Cancelled:
            raise
        except Exception:
            # A failed callback is not permission to proceed with GET.
            raise Cancelled("canonical_sync_cancelled") from None


def _valid_id(value):
    return (type(value) is str and 0 < len(value) <= 256
            and value == value.strip()
            and all(ord(ch) >= 32 and ord(ch) != 127 for ch in value))


def _account_and_session(service, credential_id):
    # Legacy dispatch is permissible ONLY when the provider ABI is partial.
    # The probe does not fetch any inventory, resolve a candidate or touch SQL.
    sessions = [
        session for session in service._sessions.values()
        if getattr(session, "provider_id", None) == "kobo"
        and getattr(session, "credential_id", None) == credential_id
        and getattr(session, "connected", None) is True
    ]
    if not any(has_complete_delivery_abi(session) for session in sessions):
        return None
    if len(sessions) != 1:
        raise ProviderConfigurationError("kobo_canonical_source_ambiguous")
    session = sessions[0]
    if not has_complete_delivery_abi(session):
        raise ProviderConfigurationError("kobo_canonical_source_ambiguous")
    accounts = [
        row for row in service.accounts
        if type(row) is Account and row.provider_id == "kobo"
        and row.credential_id == credential_id
    ]
    if len(accounts) != 1:
        raise ProviderConfigurationError("kobo_canonical_source_ambiguous")
    account = accounts[0]
    session_accounts = getattr(session, "accounts", None)
    if (not isinstance(session_accounts, (list, tuple))
            or len(session_accounts) != 1
            or type(session_accounts[0]) is not Account
            or (
                session_accounts[0].identity_id,
                session_accounts[0].provider_id,
                session_accounts[0].credential_id,
                session_accounts[0].unique_id,
            ) != (
                account.identity_id, "kobo", credential_id, account.unique_id,
            )):
        raise ProviderConfigurationError("kobo_canonical_borrower_unverified")
    return account, session


def _bound(service, account, session, *, epoch, session_epoch, policy_revision):
    """External admission check, NEVER used under canonical SQLite writer."""
    try:
        scope = scope_for_account(account)
        current = service._sessions.get(
            service._session_key("kobo", account.credential_id)
        ) is session
        current = current and source_context_epoch(service) == epoch
        current = current and getattr(session, "_epoch", None) == session_epoch
        current = current and session.connected is True
        current = current and any(row is account for row in service.accounts)
        current = current and len([
            row for row in service.accounts
            if type(row) is Account and row.provider_id == "kobo"
            and row.credential_id == account.credential_id
        ]) == 1
        current = current and len(session.accounts) == 1
        current = current and (
            session.accounts[0].identity_id,
            session.accounts[0].provider_id,
            session.accounts[0].credential_id,
            session.accounts[0].unique_id,
        ) == (scope.identity_id, scope.provider_id, scope.credential_id,
              scope.account_id)
        current = current and service.sync_source_enabled(account) is True
        current = current and service.storage.identity_for_connection(
            "kobo", account.credential_id,
        ) == account.identity_id
        current = current and service.storage.provider_scope_account_bound(
            identity_id=scope.identity_id, provider_id="kobo",
            credential_id=scope.credential_id, account_id=scope.account_id,
        ) is True
        policy = read_policy(service.storage, scope)
        return bool(current and policy.mode == "on"
                    and policy.revision == policy_revision)
    except Exception:
        return False


def _summary(status, counts=None):
    """Closed compatibility counters. Never reuse the old manifest writer."""
    counts = counts or {name: 0 for name in _COUNTS}
    complete = (
        counts["downloaded"] + counts["recovered"] + counts["present_attested"]
    )
    return {
        "scope": "account",
        "status": status,
        "canonical_execution": dict(counts),
        "execution_path": "canonical",
        "legacy_backup_mutations": 0,
        "downloaded": counts["downloaded"],
        "unchanged": counts["present_attested"],
        "completed": complete,
        "pending": counts["pending"],
        "skipped_archived": counts["skipped_archived"],
        "attention_required": counts["blocked"] + counts["inactive"],
        "errors": counts["failed"],
        "remaining": (
            counts["pending"] + counts["blocked"] + counts["failed"]
            + counts["cancelled"] + counts["inactive"]
        ),
        "cancelled": status == "cancelled",
        "aborted": False,
    }


def run_scoped_canonical_kobo_resume(
    service: Any, credential_id: str, *,
    progress: Callable[[dict[str, Any]], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
    retry_scope_blocked: bool = False,
) -> dict[str, Any] | None:
    """Dispatch *only* a modern connected Kobo source; return None for legacy.

    Complete ABI + exact borrower + persisted SourceKey-wide ON are mandatory.
    No fallback to W2 after selecting the modern route, including when its
    provider contract disappears or a callback fails halfway through.
    """
    if not _valid_id(credential_id):
        raise ProviderConfigurationError("kobo_canonical_credential_invalid")
    selected = _account_and_session(service, credential_id)
    if selected is None:
        return None
    account, session = selected
    if retry_scope_blocked is not False:
        raise ProviderConfigurationError("kobo_canonical_legacy_scope_retry_unavailable")
    control = _Cancel(cancelled)
    try:
        control.check_cancelled()
    except Cancelled:
        return _summary("cancelled")
    try:
        scope = scope_for_account(account)
        policy = read_policy(service.storage, scope)
        if (policy.mode != "on"
                or type(policy.revision) is not str
                or len(policy.revision) != 32):
            return _summary("attention_required")
        epoch = source_context_epoch(service)
        session_epoch = getattr(session, "_epoch", None)
        if not _bound(
            service, account, session, epoch=epoch,
            session_epoch=session_epoch, policy_revision=policy.revision,
        ):
            raise ProviderConfigurationError("kobo_canonical_source_unbound")
        # The canonical source Sync must acquire its OWN real global lock.
        # Never acquire the legacy lock first or run both W2 and A->B.
        report = service.synchronize_account_source(account, control=control)
        if not _bound(
            service, account, session, epoch=epoch,
            session_epoch=session_epoch, policy_revision=policy.revision,
        ):
            raise ProviderConfigurationError("kobo_canonical_source_changed")
        if (type(report) is not dict
                or report.get("provider") != "kobo"
                or report.get("account_id") != (account.unique_id or "primary")):
            raise ProviderConfigurationError("kobo_canonical_sync_unverified")
        counts = report.get("canonical_execution")
        if (type(counts) is not dict or set(counts) != _COUNTS
                or any(type(value) is not int or not 0 <= value <= 1_000_000
                       for value in counts.values())
                or sum(counts.values()) > 1_000_000):
            # An inventory-only/partial ABI cannot be reported as a completed
            # delivery. No legacy GET is attempted as recovery or fallback.
            return _summary("partial")
        if counts["cancelled"]:
            status = "cancelled"
        elif (report.get("status") in {"partial", "skipped"}
                or any(counts[key] for key in (
                    "pending", "blocked", "failed", "inactive",
                ))):
            status = "partial"
        elif report.get("status") in (None, "success"):
            status = "complete"
        else:
            status = "partial"
        result = _summary(status, counts)
        if callable(progress):
            try:
                # The compatibility CLI never receives a provider payload.
                progress({
                    "event": "done", "status": result["status"],
                    "downloaded": result["downloaded"],
                    "unchanged": result["unchanged"],
                    "attention_required": result["attention_required"],
                    "errors": result["errors"],
                })
            except Exception:
                pass
        return result
    except Cancelled:
        return _summary("cancelled")


__all__ = ["run_scoped_canonical_kobo_resume"]
