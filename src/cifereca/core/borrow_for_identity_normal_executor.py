"""#4988: normal one-shot borrow under an explicitly selected lender profile.

This path consumes a PossibleBorrowLender (local capability only), NOT
fabricated positive borrower/edition eligibility. The upstream provider
decides actual borrowing rights after ordinary human confirmation and the
same durable Core claim as a direct manual borrow. Delivery/copy stay separate.
"""
from __future__ import annotations

from inspect import getattr_static
from uuid import uuid4

from ..models import Account, SearchResult
from ..plugin_api import BorrowRequestNotSubmitted, LendingActionAdmission
from ..plugin_api.lending_capabilities import assess_lending_operation
from ..plugin_api.loan_scope import loan_matches_account
from .borrow_for_identity_action_scope import capture_cross_profile_borrow_scope
from .borrow_for_identity_discovery import (
    PossibleBorrowLender, discover_possible_borrow_lenders, possible_lender_matches,
    possible_lender_session_matches, possible_lender_claims_distinct,
)
from .borrow_for_identity_executor import _result


def execute_normal_lender_borrow(
    service, *, captured_candidate: PossibleBorrowLender,
    exact_notice: SearchResult, requesting_identity_id: str,
    scope_current, confirmed: bool = False, captured_roster=None,
) -> dict[str, object]:
    """Try exactly one normal supplier borrow, keeping B as active requester.

    Local discovery can offer a *possible* lender without pretending it
    attested capacity or edition eligibility. That fact NEVER bypasses the
    provider's ordinary claimed-request implementation. The caller must
    supply an explicit, previously confirmed user choice; a returned Loan
    with exact A/source/record coordinates is required for confirmation.
    """
    if confirmed is not True:
        return _result("blocked", "borrow_confirmation_required")
    revoked = _result("blocked", "lender_scope_revoked")
    # A single direct caller keeps its original contract. The shared detail
    # supplies the entire confirmed chooser, not only its selected member.
    roster = (captured_candidate,) if captured_roster is None else captured_roster

    def roster_current():
        try:
            return (
                possible_lender_claims_distinct(
                    roster, exact_notice, requesting_identity_id=requesting_identity_id,
                )
                and sum(row is captured_candidate for row in roster) == 1
                and all(
                    possible_lender_session_matches(
                        row, service._sessions.get(service._session_key(
                            row._account.provider_id, row._account.credential_id,
                        )),
                    ) and service.identity_by_id(row.lender_identity_id) is not None
                    for row in roster
                )
            )
        except Exception:
            return False

    try:
        if (type(captured_candidate) is not PossibleBorrowLender
                or captured_candidate.executable is not False
                or captured_candidate.supplier_eligibility != "not_verified"
                or type(exact_notice) is not SearchResult
                or captured_candidate._notice is not exact_notice
                or type(requesting_identity_id) is not str
                or not requesting_identity_id
                or not callable(scope_current)
                or scope_current() is not True or not roster_current()):
            return revoked
        requester = service.identity_by_id(requesting_identity_id)
        context_epoch = service._account_context_epoch
        source_epoch = service._source_context_epoch
        if (requester is None
                or requester.identity_id != requesting_identity_id
                or type(context_epoch) is not int or context_epoch < 0
                or type(source_epoch) is not int or source_epoch < 0):
            return revoked
        account = captured_candidate._account
        if (type(account) is not Account
                or account.identity_id == requesting_identity_id
                or not possible_lender_matches(
                    captured_candidate, exact_notice,
                    requesting_identity_id=requesting_identity_id,
                )):
            return revoked
        # Never trust the held UI candidate as a permission or lease. Re-run
        # the installed local discovery at the click boundary with the same
        # notice object. No extra account identity may be silently selected.
        choices = discover_possible_borrow_lenders(
            service, requesting_identity_id=requesting_identity_id,
            notices=(exact_notice,),
        )
        matches = [
            row for row in choices
            if (row._account is account and row._notice is exact_notice
                and row.lender_identity_id == account.identity_id
                and row.executable is False
                and row.supplier_eligibility == "not_verified")
        ]
        if len(matches) != 1 or scope_current() is not True:
            return revoked
        session = service._sessions.get(service._session_key(
            account.provider_id, account.credential_id,
        ))
        if (not possible_lender_session_matches(captured_candidate, session)
                or not possible_lender_session_matches(matches[0], session)):
            return revoked
        lease = capture_cross_profile_borrow_scope(
            service, requesting_identity_id=requesting_identity_id,
            borrowing_account=account, exact_notice=exact_notice,
            lender_session=session, scope_current=scope_current,
        )
        if lease is None:
            return revoked
        manager = service._plugin_manager
        plugin_id = service._provider_plugin_ids.get(account.provider_id)
        if type(plugin_id) is not str or not plugin_id:
            return _result("unsupported", "borrow_capability_unavailable")
        plugin = manager.status(plugin_id)
        manifest = tuple(plugin.descriptor.capabilities)
        if (plugin.enabled is not True or plugin.state != "ready"
                or plugin.descriptor.type != "provider.library"
                or plugin.descriptor.id != plugin_id):
            return _result("unsupported", "borrow_capability_unavailable")
        fact = assess_lending_operation(
            "loan.borrow", manifest_capabilities=manifest,
            descriptor=session.descriptor, session=session, account=account,
        )
        if (fact.usable is not True
                or not callable(getattr_static(
                    type(session), "borrow_explicit_with_claim", None,
                ))):
            return _result("unsupported", "borrow_capability_unavailable")
        key = lease._claim_key

        def current() -> bool:
            try:
                runtime = manager.status(plugin_id)
                descriptor = runtime.descriptor
                enabled_for = getattr(manager, "enabled_for_identity", None)
                return bool(
                    service.identity_by_id(requesting_identity_id) is requester
                    and service._account_context_epoch == context_epoch
                    and service._source_context_epoch == source_epoch
                    and scope_current() is True
                    and roster_current()
                    and possible_lender_matches(
                        captured_candidate, exact_notice,
                        requesting_identity_id=requesting_identity_id,
                    )
                    and possible_lender_session_matches(captured_candidate, session)
                    and runtime.enabled is True
                    and runtime.state == "ready"
                    and descriptor.id == plugin_id
                    and descriptor.type == "provider.library"
                    and tuple(descriptor.capabilities) == manifest
                    and callable(enabled_for)
                    and enabled_for(plugin_id, account.identity_id) is True
                    and lease.current(service)
                )
            except Exception:
                return False

        if (not current()
                or service.storage.metadata_get(key) is not None
                or service.storage.loan_borrow_state(key) is not None):
            return revoked if not current() else _result(
                "verification_unavailable", "borrow_previous_attempt",
            )
        if not current():
            return revoked
    except Exception:
        return revoked

    admission = LendingActionAdmission(
        "loan.borrow", "manual", "admitted", "admission_ready",
    )
    if not admission.admitted or not current():
        return revoked

    operation_id = uuid4().hex
    try:
        claimed, receipt = service.storage.loan_borrow_claim_once(
            key, operation_id,
        )
        if not claimed:
            return _result("verification_unavailable", "borrow_previous_attempt")
    except Exception:
        return _result("blocked", "borrow_checkpoint_unavailable")
    try:
        if not current():
            service.storage.loan_borrow_finish(
                key, operation_id, status="blocked",
            )
            return revoked
        loan = session.borrow_explicit_with_claim(
            exact_notice, account, admission=admission, claimed_receipt=receipt,
            confirmed=True, core_scope_current=current,
        )
        # No HTTP status or local candidate is success. Only the provider's
        # verified, exact lender loan can consume a positive checkpoint.
        if (not current()
                or not loan_matches_account(loan, account, session.descriptor)
                or loan.record_id != exact_notice.record_id
                or loan.provider != exact_notice.docbase
                or loan.medium != "digital"
                # The normal Paris/BnF providers both attest "active".
                # Testing only a blacklist allowed None, booleans and
                # unknown success-looking states to be confirmed.
                or type(loan.state) is not str or loan.state != "active"
                or type(loan.id) is not str or not loan.id):
            raise ValueError("unverified_lender_loan")
        finished = service.storage.loan_borrow_finish(
            key, operation_id, status="confirmed", accepted=True,
            verified_loan_id=loan.id,
        )
        if finished is None:
            return _result("verification_unavailable", "borrow_checkpoint_unavailable")
        if not current():
            return _result("verification_unavailable", "lender_scope_revoked")
        return _result(
            "confirmed", "lender_loan_confirmed_transfer_unverified",
        )
    except BorrowRequestNotSubmitted:
        try:
            released = service.storage.loan_borrow_release_unsent(
                key, operation_id,
            )
        except Exception:
            released = False
        return (
            _result("blocked", "borrow_not_submitted") if released
            else _result("verification_unavailable", "borrow_checkpoint_unavailable")
        )
    except Exception:
        # A failed/ambiguous response after dispatch may still have created
        # the upstream loan. Leave the one-shot checkpoint spent.
        try:
            service.storage.loan_borrow_finish(
                key, operation_id, status="verification_unavailable",
            )
        except Exception:
            pass
        return _result("verification_unavailable", "borrow_result_unverified")


__all__ = ["execute_normal_lender_borrow"]
