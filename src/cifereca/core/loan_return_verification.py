"""PNB/Paris/ADEPT return verification without sending a return command.

Keep the facade-owned exact source/profile resolution and original checkpoint
writes. In-flight POST age, fresh uncached inventories, incomplete-inventory
refusal and returned/token predicates retain their historical order. Logger and
exception seams are injected from LibraryService at call time; public guards and
branch-local datetime imports remain late imports.
"""
from __future__ import annotations

from inspect import getattr_static
from types import SimpleNamespace
from typing import Any
from ..models import Account, Loan


def _paris_inventory_rows_well_formed(rows: Any) -> bool:
    """A partial/corrupt row collection cannot attest a PNB loan's absence.

    The Paris adapter returns normalized Loan objects. Reject unexpected
    provider payloads before scanning for an absent loan, without invoking
    dynamic attributes, equality, stringification or repr on hostile rows.
    A valid empty inventory remains a legitimate absence proof when DILICOM
    also reports no errors.
    """
    return type(rows) is list and all(
        type(row) is Loan
        and type(row.id) is str and bool(row.id)
        and type(row.provider) is str and bool(row.provider)
        for row in rows
    )


def _paris_inventory_exact_borrower(rows: Any, account: Any) -> bool:
    """A complete source can prove absence ONLY for its intended borrower.

    The normal Paris adapter stamps every row with the source credential and
    borrower, including the primary account sentinel. A foreign member's
    rows (or rows missing these coordinates) cannot certify this member's
    loan as returned. A row may omit identity_id in the legacy Paris adapter;
    an explicitly *different* identity is never accepted.
    """
    if type(account) is not Account or not _paris_inventory_rows_well_formed(rows):
        return False
    if not (
        type(account.provider_id) is str and account.provider_id == 'paris'
        and type(account.credential_id) is str and bool(account.credential_id)
        and type(account.unique_id) is str
        and type(account.identity_id) is str and bool(account.identity_id)
    ):
        return False
    # A missing/ambiguous secondary UID must not become "primary" merely
    # because its returned loan list happens to be empty. In particular,
    # [] + a complete DILICOM status is NOT absence proof for the principal
    # account unless the selected Account really is_primary.
    if (account.is_primary is not True
            and account.unique_id in ('', 'primary')):
        return False
    borrower = account.unique_id or 'primary'
    return all(
        type(row.source_provider) is str
        and row.source_provider == account.provider_id
        and type(row.source_credential) is str
        and row.source_credential == account.credential_id
        and type(row.source_account_id) is str
        and row.source_account_id == borrower
        and (row.identity_id is None
             or (type(row.identity_id) is str
                 and row.identity_id == account.identity_id))
        for row in rows
    )



def _paris_loan_presence(rows: list[Loan], loan: Loan) -> bool | None:
    """Only an exact branch can establish presence; never guess absence.

    Provider-native loan IDs may be reused between physical SYRACUSE and
    digital DILICOM branches or change labels across a provider refresh.
    A same-ID row under a different branch is not proof that the requested
    digital loan has ended. None means its origin is ambiguous, not returned.
    Caller must first verify the exact borrower and digital completeness.
    """
    same_id = [row for row in rows if row.id == loan.id]
    if not same_id:
        return False
    # The same provider-native ID can also occur in an unrelated medium.
    # Never count a physical row as the continued presence of a digital PNB
    # right (or conversely), even if its supplier label is identical.
    return True if any(
        row.provider == loan.provider and row.medium == loan.medium
        for row in same_id
    ) else None


def _return_inventory_session_connected_inert(session: Any) -> bool:
    """Revalidate an already-selected session without invoking provider code.

    The check runs both before and after an inventory GET. A provider can
    replace an ordinary connected boolean with a property or override
    __getattribute__ during that GET. Neither may be evaluated merely to
    verify a possible loan return. Synthetic SimpleNamespace v1 sessions
    retain their original inert in-memory compatibility.
    """
    klass = type(session)
    lookup = getattr_static(klass, "__getattribute__", None)
    if lookup not in (
        object.__getattribute__, SimpleNamespace.__getattribute__,
    ):
        return False
    return getattr_static(session, "connected", None) is True


def _read_paris_return_presence(self: Any, account: Account, session: Any,
                                loan: Loan) -> bool:
    """Read one exact borrower; only typed branch coverage can prove absence.

    A v1 list of positive loans may show that an exact loan remains present,
    but neither an empty v1 list nor its mutable last_loan_errors side channel
    is a complete DILICOM pagination attestation.
    """
    epoch = getattr(self, "_account_context_epoch", None)
    accounts = self.accounts
    coordinates = (
        account.identity_id, account.provider_id,
        account.credential_id, account.unique_id,
    )

    def still_bound() -> bool:
        # Static inspection does not run provider-defined attribute getters.
        # A property or a malformed explicit closure marker is not proof
        # that the prior authenticated session remains open.
        closed = getattr_static(session, "_closed", None)
        if closed is not None and closed is not False:
            return False
        return (
            getattr(self, "_account_context_epoch", None) == epoch
            and self.accounts is accounts
            and sum(item is account for item in accounts) == 1
            and (account.identity_id, account.provider_id,
                 account.credential_id, account.unique_id) == coordinates
            and self._sessions.get(
                self._session_key(account.provider_id, account.credential_id)
            ) is session
            and _return_inventory_session_connected_inert(session)
        )

    if not still_bound():
        raise ValueError("return_inventory_source_changed")
    # A property getter is not an inventory capability and must never run
    # during return verification. Malformed declarations fail closed rather
    # than silently taking the legacy v1 route.
    raw_reader = getattr_static(session, "loan_inventory_snapshot", None)
    if raw_reader is not None and not callable(raw_reader):
        raise ValueError("return_inventory_reader_invalid")
    typed = raw_reader is not None
    if typed:
        from ..plugin_api.loan_inventory import read_loan_inventory
        snapshot = read_loan_inventory(session, account)
        if snapshot.status not in {"success", "partial"}:
            raise ValueError("return_inventory_unavailable")
        rows = list(snapshot.loans)
    else:
        # A legacy v1 result is read once for positive observations only.
        rows = session.loans(account)
    if not still_bound() or not _paris_inventory_exact_borrower(rows, account):
        raise ValueError("return_inventory_scope_changed")
    presence = _paris_loan_presence(rows, loan)
    if presence is None:
        raise ValueError("return_inventory_branch_ambiguous")
    if not presence:
        if typed:
            # Positive observations can prove a loan is still present under
            # partial coverage. Absence requires an explicit end of DILICOM
            # pagination for this *same* account and process context.
            if not snapshot.branch_claims_complete("dilicom", "digital"):
                raise ValueError("return_inventory_not_exhaustive")
        else:
            # No atomic pagination proof exists in a v1 list or error channel.
            raise ValueError("return_inventory_legacy_incomplete")
    return presence


def verify_return_digital(self, loan: Loan, *, _TRACE_LOG) -> dict[str, Any]:
    """Re-read the exact borrower with NO cache and NO second POST."""
    from .loan_return_guard import public_return_state, provider_absence_attested, return_ref
    account, session, key = self._return_loan_identity(loan)
    checkpoint = self.storage.loan_return_state(key)
    if checkpoint is None:
        return {"status": "not_requested", "scope": "provider_pnb_only"}
    if provider_absence_attested(checkpoint):
        return public_return_state(checkpoint)
    # A stored "confirmed" label without an attested absent inventory does
    # not authorize the fast-path: re-read only, never re-send the POST.
    operation_id = str(checkpoint.get("operation_id") or "")
    if checkpoint.get("status") == "request_sent":
        # A just-dispatched POST may still be in-flight. Avoid racing its
        # response with a second verification. After a crash/long stall,
        # however, permit inventory-only recovery without re-sending POST.
        from datetime import datetime, timezone
        try:
            raw_started = checkpoint["requested_at"]
            if type(raw_started) is not str or len(raw_started) > 40:
                raise ValueError("invalid checkpoint clock")
            started = datetime.fromisoformat(raw_started.replace("Z", "+00:00"))
            if started.utcoffset() is None:
                raise ValueError("naive checkpoint clock")
            age = (datetime.now(timezone.utc) - started).total_seconds()
        except (KeyError, TypeError, ValueError, OverflowError):
            age = 0
        # Small negative ages are normal clock skew and still protect the
        # in-flight POST. An implausible *future* timestamp must not freeze
        # inventory-only recovery indefinitely after a crash/restart.
        if -180 <= age < 180:
            return public_return_state(checkpoint)
    phase = "inventory_checked"
    verification = "unavailable"
    status = "verification_unavailable"
    error_type = None
    try:
        match = _read_paris_return_presence(self, account, session, loan)
        verification = "still_present" if match else "provider_absent"
        status = "still_active" if match else "provider_return_confirmed"
    except Exception:
        # The provider exception class is not a public contract. A plugin can
        # manufacture arbitrary class names, including private account data.
        error_type = "provider_inventory_unavailable"
    row = self.storage.loan_return_transition(
        key, operation_id, status=status, verification=verification,
        error_type=error_type,
    )
    _TRACE_LOG.info(
        "TRACE return core phase=%s outcome=%s ref=%s checked_at=%s",
        phase, verification, return_ref(key),
        (row or {}).get("checked_at", ""),
    )
    return public_return_state(row or checkpoint)



def verify_paris_return_inventory(self, loan: Loan, *, AttentionRequired, _TRACE_LOG) -> dict[str, str]:
    """Fresh exact-account read after ADEPT; never send ReturnDigitalLoan."""
    from .loan_return_guard import return_ref
    try:
        account, session, key = self._return_loan_identity(loan)
    except AttentionRequired:
        return {"status": "verification_unavailable", "verification": "unavailable"}
    try:
        present = _read_paris_return_presence(self, account, session, loan)
        verification = "still_present" if present else "provider_absent"
    except Exception:
        verification = "unavailable"
    _TRACE_LOG.info(
        "TRACE return core phase=paris_inventory outcome=%s ref=%s",
        verification, return_ref(key),
    )
    return {
        "verification": verification,
        "status": {
            "provider_absent": "provider_return_confirmed",
            "still_present": "still_active",
            "unavailable": "verification_unavailable",
        }[verification],
    }



def adobe_return_inventory_confirmed(self, acquisition: dict[str, Any]) -> bool:
    """Crash recovery: ADEPT inventory check ONLY, never replay mutation.

    Legacy SQLite acquisition metadata is not an authorization boundary:
    malformed lists/strings and arbitrary profile/loan objects must fail
    closed *before* any runtime lookup. A partial/ambiguous loan registry is
    never evidence that an Adobe return has happened.
    """
    if type(acquisition) is not dict:
        return False
    metadata = acquisition.get("metadata")
    if type(metadata) is not dict:
        return False
    binding = metadata.get("adept_binding")
    if type(binding) is not dict:
        return False
    profile_id = binding.get("adept_profile_id")
    loan_id = binding.get("adept_loan_id")
    if any(type(value) is not str or not 0 < len(value) <= 256
           for value in (profile_id, loan_id)):
        return False
    try:
        profile = self._adept_profile_row(profile_id, self.kobo_profiles())
        if not profile:
            return False
        inventory = self._kobo_adapter().loans(self._adept_profile_name(profile))
        if type(inventory) is not dict or type(inventory.get("loans")) is not list:
            return False
        rows = inventory["loans"]
        if any(type(row) is not dict or type(row.get("id")) is not str
               for row in rows):
            # A partial/hostile registry cannot prove the exact transaction.
            return False
        matches = [row for row in rows if row["id"] == loan_id]
        return (
            len(matches) == 1
            and matches[0].get("returned") is True
            and matches[0].get("token_present") is False
        )
    except Exception:
        return False

