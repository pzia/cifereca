"""Guarded provider-only loan return behind LibraryService's existing lock.

Keep the durable SQLite claim, Adobe checkpoint precedence, uncertain POST
handling and fresh verification/retry order. No Adobe mutation or acquisition
is added. The facade injects its logger; UUID and public guards remain late
imports at their historical execution points.
"""
from __future__ import annotations
from typing import Any
from ..models import Loan


def return_digital(self, loan: Loan, *, explicit_retry: bool = False, _TRACE_LOG) -> dict[str, Any]:
    """PNB provider return with an at-most-once POST until verified.

    An HTTP success only means REQUEST ACCEPTED. No Adobe return is made.
    If a prior request exists, normal repeated clicks never send POST.
    An explicit retry may POST only after a fresh read proves still active.
    """
    # This helper is also directly callable through the guarded service facade.
    # Only the native True can authorize a second PNB POST. Truthy strings,
    # integers or foreign objects must not bypass the durable at-most-once
    # checkpoint after a timeout, restart or already verified Adobe return.
    explicit_retry = explicit_retry is True
    import uuid
    from .loan_return_guard import public_return_state, return_ref
    if loan.source_provider != "paris":
        # Preserve legacy non-Paris providers; this guard is Paris-only.
        account = self._account_for_loan(loan)
        session = self._session_for_source(loan.source_provider, loan.source_credential)
        return session.return_digital(loan, account)
    account, session, key = self._return_loan_identity(loan)
    if self._cross_identity_loan_conflict(
        self.storage.acquisitions(provider_id=loan.source_provider),
        provider_loan_id=loan.id, identity_id=account.identity_id,
        entitlements=self.storage.entitlements(
            provider_id=loan.source_provider, state="active"
        ),
    ):
        return {
            "status": "attention_required", "scope": "provider_pnb_only",
            "code": "loan_return_identity_conflict",
            "retry": "resolve_identity",
        }
    from .return_adept_orchestrator import (
        adept_return_key, adobe_return_attested, public_state,
    )
    adobe_key = adept_return_key(
        loan.source_provider, loan.source_credential,
        str(account.unique_id or "primary"), str(loan.id),
    )
    adobe = self.storage.loan_return_state(adobe_key)
    if adobe is not None:
        # A lone adobe_verified=True on request_sent, failed or pending
        # cannot authorize a PNB mutation. Require the exact same attested
        # Adobe phase that the public return state already trusts.
        if not explicit_retry or not adobe_return_attested(adobe):
            return {
                **public_state(adobe), "reason": "adobe_return_takes_precedence",
            }
        fresh = self.verify_paris_return_inventory(loan)
        if fresh.get("status") != "still_active":
            return fresh
    prior = self.storage.loan_return_state(key)
    if prior is not None:
        if not explicit_retry:
            return public_return_state(prior)
        verified = self.verify_return_digital(loan)
        if verified.get("status") != "still_active":
            return verified
    operation_id = uuid.uuid4().hex
    claimed, state = self.storage.loan_return_claim(
        key, operation_id, reopen=bool(prior and explicit_retry),
    )
    if not claimed:
        return public_return_state(state)
    _TRACE_LOG.info("TRACE return core phase=request_sent ref=%s", return_ref(key))
    try:
        response = session.return_digital(loan, account)
        if not isinstance(response, dict) or response.get("success") is not True:
            raise ValueError("Paris did not attest request acceptance")
    except Exception:
        # Network failures are indeterminate: POST may already have been
        # accepted upstream. Never retry automatically on uncertainty.
        # Plugin-controlled exception class names are not safe for either
        # the durable ledger or the operator TRACE.
        row = self.storage.loan_return_transition(
            key, operation_id, status="verification_unavailable",
            verification="unavailable", error_type="provider_return_unavailable",
        )
        _TRACE_LOG.warning(
            "TRACE return core phase=request_uncertain ref=%s code=provider_return_unavailable",
            return_ref(key),
        )
        return public_return_state(row or state)
    row = self.storage.loan_return_transition(
        key, operation_id, status="pending_verification",
        verification="not_checked", accepted=True,
    )
    _TRACE_LOG.info(
        "TRACE return core phase=request_accepted ref=%s provider_inventory_verified=false",
        return_ref(key),
    )
    # Stale cached loans must not override verification after accepted POST.
    self.storage.cache_delete_namespace("account")
    return self.verify_return_digital(loan)

