"""PNB/Paris/ADEPT return verification without sending a return command.

Keep the facade-owned exact source/profile resolution and original checkpoint
writes. In-flight POST age, fresh uncached inventories, incomplete-inventory
refusal and returned/token predicates retain their historical order. Logger and
exception seams are injected from LibraryService at call time; public guards and
branch-local datetime imports remain late imports.
"""
from __future__ import annotations
from typing import Any
from ..models import Loan


def _paris_digital_inventory_complete(session: Any) -> bool:
    """Only SYRACUSE errors are demonstrably unrelated to digital DILICOM.

    Paris's official adapter always returns a list of error dictionaries
    labelled DILICOM or SYRACUSE. Missing, malformed or unknown error entries
    cannot attest a complete digital inventory. Inspect only exact types and
    known codes; never stringify or invoke provider-controlled comparisons.
    """
    errors = getattr(session, "last_loan_errors", None)
    return type(errors) in (list, tuple) and all(
        type(error) is dict
        and type(error.get("upstream")) is str
        and error["upstream"] == "SYRACUSE"
        for error in errors
    )


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
        loans = session.loans(account)  # ALWAYS direct provider; bypass cached account reads
        if not isinstance(loans, list):
            raise ValueError("provider inventory has no complete list")
        # A failed SYRACUSE leg is unrelated to digital loans. Any missing
        # or unrecognized provider error is ambiguous and fails closed.
        if loan.source_provider == "paris" and not _paris_digital_inventory_complete(session):
            raise ValueError("Paris digital-loan inventory incomplete")
        match = any(
            candidate.id == loan.id and candidate.provider == loan.provider
            for candidate in loans
        )
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
        rows = session.loans(account)
        if not isinstance(rows, list):
            raise ValueError("Paris inventory incomplete")
        if not _paris_digital_inventory_complete(session):
            raise ValueError("Paris digital inventory unverified")
        present = any(
            row.id == loan.id and row.provider == loan.provider for row in rows
        )
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
    """Crash recovery: ADEPT inventory check ONLY, never replay mutation."""
    binding = (acquisition.get("metadata") or {}).get("adept_binding") or {}
    profile_id = binding.get("adept_profile_id")
    loan_id = binding.get("adept_loan_id")
    if not profile_id or not loan_id:
        return False
    try:
        profile = self._adept_profile_row(profile_id, self.kobo_profiles())
        if not profile:
            return False
        loans = self._kobo_adapter().loans(self._adept_profile_name(profile))
        matches = [r for r in loans["loans"] if r.get("id") == loan_id]
        return (
            len(matches) == 1
            and matches[0].get("returned") is True
            and matches[0].get("token_present") is False
        )
    except Exception:
        return False

