"""P0 #2055: Core-owned admission for a *manual* BnF new loan.

This dispatcher is intentionally dormant with the currently published BnF
plugin: acquisition.borrow is absent and provider permissions are not attested.
No browse/search/sync caller may invoke it. Core alone owns consent, the
inert scope lease, capability/borrower checks and durable at-most-once claim.
"""
from __future__ import annotations

from inspect import getattr_static
from uuid import uuid4

from ..models import Account, Loan, SearchResult
from ..plugin_api import (
    BorrowerOperationEvidence, assess_borrower_eligibility,
    assess_lending_action,
)
from ..plugin_api.lending_capabilities import assess_lending_operation
from ..providers.base import ProviderSession
from .borrow_action_scope import capture_borrow_action_scope, borrow_claim_key
from .loan_action_scope import _inert_session_accounts, _inert_session_state


def _result(status: str, reason: str, *, verified: bool = False):
    """Public outcome: no raw URL, title, account, CSRF, loan ID or exception."""
    return {
        "status": status, "reason_code": reason,
        "provider_verified": verified is True,
        "retry": "verification_only" if status in {
            "confirmed", "verification_unavailable", "submitted",
        } else "manual_only",
    }


def manual_bnf_borrow(
    service, item: SearchResult, *, expected_identity_id: str,
    confirmed: bool = False,
) -> dict[str, object]:
    """Select the exact live BnF account and admit at most one future POST.

    The provider must separately supply a fresh `BorrowerOperationEvidence`
    scoped to the exact account+resource; a catalogue button, successful
    login, syntactically valid form, empty inventory, client-side callback or
    arbitrary Loan DTO is never positive permission. As of 0.3.18 this
    provider evidence API does not exist, so the action remains blocked.
    """
    if confirmed is not True:
        return _result("blocked", "borrow_confirmation_required")
    if (type(item) is not SearchResult
            or type(expected_identity_id) is not str
            or not expected_identity_id
            or item.source_provider != "bnf-pret"
            or item.docbase != "BNF_PRET"
            or item.medium != "digital"):
        return _result("blocked", "borrow_scope_unverified")
    try:
        manager = service._plugin_manager
        plugin_id = service._provider_plugin_ids["bnf-pret"]
        plugin = manager.status(plugin_id)
        if (plugin.state != "ready"
                or plugin.descriptor.type != "provider.library"
                or plugin.descriptor.id != plugin_id):
            return _result("blocked", "borrow_capability_unavailable")
        session = service._session_for_source(
            "bnf-pret", item.source_credential)
        state = _inert_session_state(session)
        registered = _inert_session_accounts(session)
        if state is None or registered is None:
            return _result("blocked", "borrow_scope_unverified")
        candidates = [
            account for account in service.accounts
            if (type(account) is Account
                and account.identity_id == expected_identity_id
                and account.provider_id == "bnf-pret"
                and account.credential_id == item.source_credential
                and sum(row is account for row in registered) == 1)
        ]
        if len(candidates) != 1:
            return _result("blocked", "borrow_scope_unverified")
        account = candidates[0]
        lease = capture_borrow_action_scope(
            service, item, account, session,
            expected_identity_id=expected_identity_id)
        key = borrow_claim_key(account, item)
        if lease is None or key is None:
            return _result("blocked", "borrow_scope_unverified")
        # Canonical installed PluginManager descriptor is the manifest source;
        # never accept a capability fabricated by a caller or the selected row.
        fact = assess_lending_operation(
            "loan.borrow",
            manifest_capabilities=tuple(plugin.descriptor.capabilities),
            descriptor=session.descriptor, session=session, account=account,
        )
        if not fact.usable:
            return _result("unsupported", "borrow_capability_unavailable")
        if not callable(getattr_static(type(session), "borrower_eligibility_for", None)):
            return _result("blocked", "borrow_permission_unverified")
        if not callable(getattr_static(type(session), "borrow_explicit_with_claim", None)):
            return _result("unsupported", "borrow_execution_unavailable")
        prior = service.storage.loan_borrow_state(key)
        if prior is not None:
            return _result("verification_unavailable", "borrow_previous_attempt")
    except Exception:
        return _result("blocked", "borrow_scope_unverified")

    try:
        # Provider supplies an *observed* scoped borrower/edition entitlement,
        # never a cached UI hint. No Core POST or claim on unknown evidence.
        evidence = session.borrower_eligibility_for(item, account)
    except Exception:
        return _result("blocked", "borrow_permission_unverified")
    if not lease.current(service):
        return _result("blocked", "borrow_scope_changed")
    if type(evidence) is not BorrowerOperationEvidence:
        return _result("blocked", "borrow_permission_unverified")
    eligibility = assess_borrower_eligibility(
        capability=fact, account=account, notice=item, evidence=evidence,
    )
    if eligibility.status != "eligible":
        return _result("blocked", "borrow_permission_unverified")
    admission = assess_lending_action(
        "loan.borrow", fact=fact, account=account, target=item,
        origin="manual", confirmed=True, permission_attested=True,
    )
    if not admission.admitted or not lease.current(service):
        return _result("blocked", "borrow_scope_changed")

    operation_id = uuid4().hex
    try:
        claimed, receipt = service.storage.loan_borrow_claim_once(
            key, operation_id)
        if not claimed:
            return _result("verification_unavailable", "borrow_previous_attempt")
    except Exception:
        return _result("blocked", "borrow_checkpoint_unavailable")
    try:
        # A concurrent rebind after the durable claim still forbids the POST.
        # The consumed claim is NOT reopened on this failure.
        if not lease.current(service):
            service.storage.loan_borrow_finish(
                key, operation_id, status="blocked")
            return _result("blocked", "borrow_scope_changed")
        loan = session.borrow_explicit_with_claim(
            item, account, admission=admission, claimed_receipt=receipt,
            confirmed=True, core_scope_current=lambda: lease.current(service),
        )
        if (not lease.current(service) or type(loan) is not Loan
                or loan.record_id != item.record_id
                or loan.source_provider != "bnf-pret"
                or loan.source_credential != account.credential_id
                or loan.source_account_id != account.unique_id
                or loan.identity_id != account.identity_id
                or loan.medium != "digital" or loan.state != "active"
                or type(loan.id) is not str or not loan.id):
            raise ValueError("loan_verification_unavailable")
        finished = service.storage.loan_borrow_finish(
            key, operation_id, status="confirmed",
            accepted=True, verified_loan_id=loan.id,
        )
        if finished is None:
            return _result("verification_unavailable", "borrow_checkpoint_unavailable")
        return _result("confirmed", "borrow_active_loan_observed", verified=True)
    except Exception:
        # A timeout/500/rebind after dispatch may have created a real upstream
        # loan; NEVER issue a new borrow POST or free the consumed claim.
        try:
            service.storage.loan_borrow_finish(
                key, operation_id, status="verification_unavailable")
        except Exception:
            pass
        return _result("verification_unavailable", "borrow_result_unverified")


__all__ = ["manual_bnf_borrow"]
