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


def verify_return_digital(self, loan: Loan, *, _TRACE_LOG) -> dict[str, Any]:
    """Re-read the exact borrower with NO cache and NO second POST."""
    from .loan_return_guard import public_return_state, return_ref
    account, session, key = self._return_loan_identity(loan)
    checkpoint = self.storage.loan_return_state(key)
    if checkpoint is None:
        return {"status": "not_requested", "scope": "provider_pnb_only"}
    if checkpoint.get("status") == "provider_return_confirmed":
        return public_return_state(checkpoint)
    operation_id = str(checkpoint.get("operation_id") or "")
    if checkpoint.get("status") == "request_sent":
        # A just-dispatched POST may still be in-flight. Avoid racing its
        # response with a second verification. After a crash/long stall,
        # however, permit inventory-only recovery without re-sending POST.
        from datetime import datetime, timezone
        try:
            started = datetime.fromisoformat(str(checkpoint["requested_at"]))
            age = (datetime.now(timezone.utc) - started).total_seconds()
        except (KeyError, TypeError, ValueError):
            age = 0
        if age < 180:
            return public_return_state(checkpoint)
    phase = "inventory_checked"
    verification = "unavailable"
    status = "verification_unavailable"
    error_type = None
    try:
        loans = session.loans(account)  # ALWAYS direct provider; bypass cached account reads
        if not isinstance(loans, list):
            raise ValueError("provider inventory has no complete list")
        # Paris can recover from an unrelated SYRACUSE failure; a failed
        # DILICOM leg NEVER proves absence of a digital loan.
        upstream_errors = getattr(session, "last_loan_errors", None)
        if loan.source_provider == "paris" and upstream_errors is None:
            raise ValueError("Paris session lacks inventory-verification capability")
        if upstream_errors and any(
            str(e.get("upstream") or "") == "DILICOM" for e in upstream_errors
            if isinstance(e, dict)
        ):
            raise ValueError("digital-loan inventory was incomplete")
        match = any(
            candidate.id == loan.id and candidate.provider == loan.provider
            for candidate in loans
        )
        verification = "still_present" if match else "provider_absent"
        status = "still_active" if match else "provider_return_confirmed"
    except Exception as exc:
        error_type = type(exc).__name__
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
        errors = getattr(session, "last_loan_errors", None)
        if errors is None or any(
            isinstance(error, dict) and error.get("upstream") == "DILICOM"
            for error in errors
        ):
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

