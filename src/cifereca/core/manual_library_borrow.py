"""Core-owned manual admission and durable claim, shared by BnF and Paris.

The source wrappers fix the upstream branch and primary-account convention.
Only the provider implements the remote protocol; no Sync or ADEPT runs here.
"""
from __future__ import annotations

import logging
from inspect import getattr_static
from uuid import uuid4

from ..models import Account, Loan, SearchResult
from ..plugin_api import LendingActionAdmission, BorrowRequestNotSubmitted
from ..plugin_api.lending_capabilities import assess_lending_operation
from .borrow_action_scope import capture_borrow_action_scope, borrow_claim_key
from .loan_action_scope import _inert_session_accounts, _inert_session_state

_LOG = logging.getLogger(__name__)


def _result(status: str, reason: str, *, verified: bool = False):
    """Public outcome: no raw URL, title, account, CSRF, loan ID or exception."""
    return {
        "status": status, "reason_code": reason,
        "provider_verified": verified is True,
        "retry": "verification_only" if status in {
            "confirmed", "verification_unavailable", "submitted",
        } else "manual_only",
    }


def manual_borrow_available(service, item: SearchResult, *, provider_id: str,
                            docbase: str, primary_id: str | None = None,
                            diagnostic: dict[str, str] | None = None) -> bool:
    """Inert local admission, never supplier permission or a provider GET.

    Optional diagnostic is a CLOSED-code side channel, not new authorization.
    The boolean outcome and all existing safeguards are unchanged.
    """
    if diagnostic is not None:
        diagnostic.clear()

    def reject(code: str) -> bool:
        if diagnostic is not None:
            diagnostic["reason_code"] = code
        return False

    if (type(item) is not SearchResult or item.source_provider != provider_id
            or item.docbase != docbase or item.medium != "digital"):
        return reject("local_edition_scope_invalid")
    try:
        manager = service._plugin_manager
        plugin_id = service._provider_plugin_ids[provider_id]
        plugin = manager.status(plugin_id)
        identity = service.current_identity
        if (plugin.enabled is not True or plugin.state != "ready"
                or plugin.descriptor.type != "provider.library"
                or plugin.descriptor.id != plugin_id
                or not getattr(identity, "identity_id", None)):
            return reject("local_provider_or_identity_unavailable")
        session = service._session_for_source(
            provider_id, item.source_credential)
        rows = _inert_session_accounts(session)
        state = _inert_session_state(session)
        if (rows is None or state is None
                or state.get("connected") is not True
                or state.get("_closed") is True):
            return reject("local_session_unavailable")
        candidates = [
            a for a in service.accounts
            if (type(a) is Account and a.is_primary is True
                and a.identity_id == identity.identity_id
                and a.provider_id == provider_id
                and a.credential_id == item.source_credential
                and (primary_id is None or a.unique_id == primary_id)
                and sum(r is a for r in rows) == 1)
        ]
        if len(candidates) != 1:
            return reject("local_primary_account_unverified")
        claim_key = borrow_claim_key(candidates[0], item)
        if claim_key is None:
            return reject("local_claim_scope_invalid")
        # A malformed prior checkpoint is spent even if decoding returns None.
        if service.storage.metadata_get(claim_key) is not None:
            return reject("local_previous_attempt")
        if capture_borrow_action_scope(
                service, item, candidates[0], session,
                expected_identity_id=identity.identity_id) is None:
            return reject("local_scope_changed")
        fact = assess_lending_operation(
            "loan.borrow",
            manifest_capabilities=tuple(plugin.descriptor.capabilities),
            descriptor=session.descriptor, session=session,
            account=candidates[0],
        )
        if not fact.usable:
            return reject("local_borrow_capability_unavailable")
        if not callable(getattr_static(
                type(session), "borrow_explicit_with_claim", None)):
            return reject("local_borrow_executor_unavailable")
        if diagnostic is not None:
            diagnostic["reason_code"] = "local_action_available_provider_unknown"
        return True
    except Exception:
        return reject("local_admission_unavailable")


def manual_borrow(
    service, item: SearchResult, *, provider_id: str, docbase: str,
    primary_id: str | None = None, expected_identity_id: str,
    confirmed: bool = False, site_checked: bool = False,
    expected_context_epoch: int | None = None,
    expected_account: Account | None = None,
    additional_scope_current=None,
) -> dict[str, object]:
    """Select the exact live lender account and admit at most one attempt.

    The explicit user action is signaled by confirmed=True; the deprecated
    site_checked argument is ignored for source compatibility, not used as an
    invented permission or website attestation. The provider verifies the
    current loan independently of ACSM or final-file delivery.
    A visible button and HTTP 200 alone never prove success.
    """
    if confirmed is not True:
        return _result("blocked", "borrow_confirmation_required")
    # A provider session may be replaced/rebound without changing either the
    # selected profile or the account epoch. Pin this Core-wide source epoch
    # before provider inspection and revoke at every later mutating boundary.
    source_epoch = getattr(service, "_source_context_epoch", None)
    if type(source_epoch) is not int or source_epoch < 0:
        return _result("blocked", "borrow_scope_unverified")
    # #3121: no mandatory visual check or additional confirmation. Retain
    # the old keyword for external callers only; it must not change admission.
    if (expected_context_epoch is not None
            and (type(expected_context_epoch) is not int
                 or type(getattr(service, "_account_context_epoch", None)) is not int
                 or service._account_context_epoch != expected_context_epoch)):
        return _result("blocked", "borrow_scope_changed")
    if expected_account is not None and type(expected_account) is not Account:
        return _result("blocked", "borrow_scope_unverified")
    if (type(item) is not SearchResult
            or type(expected_identity_id) is not str
            or not expected_identity_id
            or item.source_provider != provider_id
            or item.docbase != docbase
            or item.medium != "digital"):
        return _result("blocked", "borrow_scope_unverified")
    try:
        manager = service._plugin_manager
        plugin_id = service._provider_plugin_ids[provider_id]
        plugin = manager.status(plugin_id)
        if (plugin.state != "ready" or plugin.enabled is not True
                or plugin.descriptor.type != "provider.library"
                or plugin.descriptor.id != plugin_id):
            return _result("blocked", "borrow_capability_unavailable")
        session = service._session_for_source(
            provider_id, item.source_credential)
        state = _inert_session_state(session)
        registered = _inert_session_accounts(session)
        if state is None or registered is None:
            return _result("blocked", "borrow_scope_unverified")
        candidates = [
            account for account in service.accounts
            if (type(account) is Account and account.is_primary is True
                and account.identity_id == expected_identity_id
                and account.provider_id == provider_id
                and account.credential_id == item.source_credential
                and (primary_id is None or account.unique_id == primary_id)
                and sum(row is account for row in registered) == 1)
        ]
        if len(candidates) != 1:
            return _result("blocked", "borrow_scope_unverified")
        account = candidates[0]
        if expected_account is not None and account is not expected_account:
            return _result("blocked", "borrow_scope_changed")
        lease = capture_borrow_action_scope(
            service, item, account, session,
            expected_identity_id=expected_identity_id)
        key = borrow_claim_key(account, item)
        if lease is None or key is None:
            return _result("blocked", "borrow_scope_unverified")
        def current():
            return (getattr(service, "_source_context_epoch", None)
                    == source_epoch and lease.current(service) and (
                expected_context_epoch is None
                or service._account_context_epoch == expected_context_epoch)
                and (additional_scope_current is None
                     or additional_scope_current() is True))
        # Canonical installed PluginManager descriptor is the manifest source;
        # never accept a capability fabricated by a caller or the selected row.
        fact = assess_lending_operation(
            "loan.borrow",
            manifest_capabilities=tuple(plugin.descriptor.capabilities),
            descriptor=session.descriptor, session=session, account=account,
        )
        if not fact.usable:
            return _result("unsupported", "borrow_capability_unavailable")
        if not callable(getattr_static(type(session), "borrow_explicit_with_claim", None)):
            return _result("unsupported", "borrow_execution_unavailable")
        prior = service.storage.loan_borrow_state(key)
        if prior is not None:
            return _result("verification_unavailable", "borrow_previous_attempt")
    except Exception:
        return _result("blocked", "borrow_scope_unverified")

    # Explicitly scoped human-initiated attempt; no automatic borrowing.
    # Do NOT call assess_lending_action(permission_attested=True):
    # that shared API requires machine-attested rights, which a UI click
    # cannot supply. This admission only permits the *attempt*.
    # The lender decides eligibility on its fresh request; HTTP 200 is not success
    # without an independently observed active loan in the same account.
    if not current():
        return _result("blocked", "borrow_scope_changed")
    admission = LendingActionAdmission(
        "loan.borrow", "manual", "admitted", "admission_ready",
    )
    if not admission.admitted or not current():
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
        # A concurrent rebind after the durable claim still forbids the remote action.
        # The consumed claim is NOT reopened on this failure.
        if not current():
            service.storage.loan_borrow_finish(
                key, operation_id, status="blocked")
            return _result("blocked", "borrow_scope_changed")
        loan = session.borrow_explicit_with_claim(
            item, account, admission=admission, claimed_receipt=receipt,
            confirmed=True, core_scope_current=lambda: current(),
        )
        if (not current() or type(loan) is not Loan
                or loan.record_id != item.record_id
                or loan.source_provider != provider_id
                or loan.source_credential != account.credential_id
                or loan.source_account_id != (
                    "primary" if primary_id == "" else account.unique_id)
                or loan.identity_id != account.identity_id
                or loan.provider != docbase or loan.medium != "digital"
                # Paris returns the fresh current-inventory state verbatim
                # (including numeric states). Its claimed-borrow ABI verifies
                # that observation independently; no owned/active right is
                # synthesized here. BnF's active-state requirement is retained.
                or (primary_id is None and loan.state != "active")
                or (primary_id is not None and loan.state in (
                    "returned", "expired", "inactive"))
                or type(loan.id) is not str or not loan.id):
            raise ValueError("loan_verification_unavailable")
        finished = service.storage.loan_borrow_finish(
            key, operation_id, status="confirmed",
            accepted=True, verified_loan_id=loan.id,
        )
        if finished is None:
            return _result("verification_unavailable", "borrow_checkpoint_unavailable")
        if provider_id == "paris":
            # #3121: the first signed ACSM offer exists only on the fresh Loan
            # returned by the claimed borrow. Stage *after* the durable
            # confirmed checkpoint, by a guarded, read-only token GET. Its
            # failure does not negate the separately verified lender loan.
            try:
                from .paris_first_acsm_receipt import stage_paris_initial_acsm
                staged = stage_paris_initial_acsm(
                    service, session, account, loan, scope_current=current,
                    claim_key=key, operation_id=operation_id)
                verdict = "staged" if staged is True else "refused"
                reason = ("verified_initial_receipt" if staged is True
                          else "initial_candidate_unavailable")
            except Exception:
                # The borrow claim is already durably confirmed. A local
                # staging/config/import fault cannot unconfirm it or reopen
                # the mutating supplier request. Never log the exception:
                # it can contain a bearer URL, loan ID or local ACSM path.
                verdict, reason = "refused", "initial_staging_error"
            _LOG.info(
                "TRACE paris_first_acsm outcome=%s reason_code=%s",
                verdict, reason,
            )
        return _result("confirmed", "borrow_active_loan_observed", verified=True)
    except BorrowRequestNotSubmitted:
        # The trusted provider raises this ONLY before attempting a mutating request.
        # A failed read-only precheck must not permanently consume the
        # edition's claim. CAS release still requires this exact operation
        # and the original request_sent state; no retry happens here.
        try:
            freed = service.storage.loan_borrow_release_unsent(
                key, operation_id)
        except Exception:
            freed = False
        return (_result("blocked", "borrow_not_submitted") if freed
                else _result("verification_unavailable",
                             "borrow_checkpoint_unavailable"))
    except Exception:
        # A timeout/500/rebind after dispatch may have created a real upstream
        # loan; NEVER issue a new borrow request or free the consumed claim.
        try:
            service.storage.loan_borrow_finish(
                key, operation_id, status="verification_unavailable")
        except Exception:
            pass
        return _result("verification_unavailable", "borrow_result_unverified")


__all__ = ["manual_borrow", "manual_borrow_available"]
