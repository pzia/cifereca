"""#4987: one explicit cross-profile borrowing attempt, without profile switch.

A verified lender loan stays on the lender. Delivery, ADEPT and transfer are
separate operations with independent rights and MUST NOT be inferred here.
"""
from __future__ import annotations

from inspect import getattr_static
from uuid import uuid4

from ..models import Account, Loan
from ..plugin_api import LendingActionAdmission, BorrowRequestNotSubmitted
from ..plugin_api.lending_capabilities import assess_lending_operation
from .borrow_for_identity_action_scope import capture_cross_profile_borrow_scope
from .borrow_for_identity_live import (
    ObservedBorrowOffer, _witness, revalidate_live_borrow_lender_selection,
)
from .borrow_for_identity_preview import BorrowForIdentityPreview, BorrowForIdentityWitness


def _result(status: str, reason: str) -> dict[str, object]:
    # Neither claim, source coordinates nor response payload are public.
    return {
        "status": status, "reason_code": reason,
        "provider_verified": status == "confirmed",
        "delivery": "unavailable", "transfer": "blocked",
        "retry_borrow": False,
    }


def execute_cross_profile_borrow(
    service, *, captured_preview: BorrowForIdentityPreview,
    selected_option_id: str, expected_edition_id: str,
    observations: object, confirmed: bool = False, now: int | None = None,
) -> dict[str, object]:
    """Admit one explicitly selected lender POST under Core's durable claim.

    Both Core and the provider independently gate a mutation. The provider
    owns the fresh upstream eligibility check, not a cached search notice.
    """
    if confirmed is not True:
        return _result("blocked", "borrow_confirmation_required")
    revoked = _result("blocked", "lender_scope_revoked")
    try:
        if (type(captured_preview) is not BorrowForIdentityPreview
                or captured_preview.executable is not False
                or type(selected_option_id) is not str
                or type(expected_edition_id) is not str
                or type(observations) not in (list, tuple)
                or len(observations) > 64
                or any(type(row) is not ObservedBorrowOffer for row in observations)):
            return revoked
        requester = service.current_identity
        context_epoch = service._account_context_epoch
        source_epoch = service._source_context_epoch
        if (type(context_epoch) is not int or context_epoch < 0
                or type(source_epoch) is not int or source_epoch < 0
                or getattr(requester, "identity_id", None)
                    != captured_preview.requesting_identity_id):
            return revoked
        selection = revalidate_live_borrow_lender_selection(
            service, captured_preview=captured_preview,
            selected_option_id=selected_option_id,
            expected_edition_id=expected_edition_id,
            observations=observations, now=now,
        )
        if (selection.status != "current" or selection.executable is not False
                or not selection.borrowing_identity_id
                or selection.borrowing_identity_id ==
                    captured_preview.requesting_identity_id):
            return revoked
        options = [
            option for option in captured_preview.options
            if option.option_id == selected_option_id
            and option.executable is False
            and option.borrowing_identity_id == selection.borrowing_identity_id
        ]
        if len(options) != 1:
            return revoked
        option = options[0]
        matched: list[BorrowForIdentityWitness] = []
        for row in observations:
            witness = _witness(
                service, row, context_epoch=context_epoch,
                source_epoch=source_epoch,
            )
            if type(witness) is not BorrowForIdentityWitness:
                continue
            account, notice = witness.account, witness.notice
            coordinates = (
                account.identity_id, account.provider_id, account.credential_id,
                account.unique_id, notice.docbase, notice.record_id,
                notice.source_collection or "", witness.edition_id,
            )
            if (coordinates == option._source_coordinates
                    and witness.edition_id == expected_edition_id):
                matched.append(witness)
        # Never first-match duplicate evidence, including same-account
        # multiple UI rows. The user must reselect an unambiguous offer.
        if len(matched) != 1:
            return revoked
        w = matched[0]
        account, notice = w.account, w.notice
        if (type(account) is not Account
                or account.identity_id != selection.borrowing_identity_id
                or notice.medium != "digital"):
            return revoked
        session = service._sessions.get(service._session_key(
            account.provider_id, account.credential_id,
        ))
        lease = capture_cross_profile_borrow_scope(
            service, requesting_identity_id=captured_preview.requesting_identity_id,
            borrowing_account=account, exact_notice=notice,
            lender_session=session,
        )
        if lease is None:
            return revoked
        manager = service._plugin_manager
        plugin_id = service._provider_plugin_ids[account.provider_id]
        plugin = manager.status(plugin_id)
        if (plugin.enabled is not True or plugin.state != "ready"
                or plugin.descriptor.type != "provider.library"
                or plugin.descriptor.id != plugin_id):
            return _result("unsupported", "borrow_capability_unavailable")
        fact = assess_lending_operation(
            "loan.borrow",
            manifest_capabilities=tuple(plugin.descriptor.capabilities),
            descriptor=session.descriptor, session=session, account=account,
        )
        if (not fact.usable or not callable(getattr_static(
                type(session), "borrow_explicit_with_claim", None))):
            return _result("unsupported", "borrow_capability_unavailable")
        key = lease._claim_key
        manifest = tuple(plugin.descriptor.capabilities)

        def current() -> bool:
            try:
                runtime = manager.status(plugin_id)
                descriptor = runtime.descriptor
                enabled_for = getattr(manager, "enabled_for_identity", None)
                if (runtime.enabled is not True or runtime.state != "ready"
                        or descriptor.id != plugin_id
                        or descriptor.type != "provider.library"
                        or tuple(descriptor.capabilities) != manifest
                        or not callable(enabled_for)
                        or enabled_for(
                            plugin_id, account.identity_id,
                        ) is not True):
                    return False
                # Lease is last: reject context/source/account changes
                # caused during plugin manager or identity-status lookup.
                return (lease.current(service)
                        and service.current_identity is requester
                        and service._account_context_epoch == context_epoch
                        and service._source_context_epoch == source_epoch)
            except Exception:
                return False

        if not current():
            return revoked
        # Read corrupt historical raw checkpoints too: never treat a broken
        # decoder's None as a fresh unspent borrow.
        if (service.storage.metadata_get(key) is not None
                or service.storage.loan_borrow_state(key) is not None):
            return _result("verification_unavailable", "borrow_previous_attempt")
        if not current():
            return revoked
    except Exception:
        return revoked

    admission = LendingActionAdmission(
        "loan.borrow", "manual", "admitted", "admission_ready",
    )
    if not admission.admitted or not current():
        return revoked
    op = uuid4().hex
    try:
        claimed, receipt = service.storage.loan_borrow_claim_once(key, op)
        if not claimed:
            return _result("verification_unavailable", "borrow_previous_attempt")
    except Exception:
        return _result("blocked", "borrow_checkpoint_unavailable")
    try:
        if not current():
            service.storage.loan_borrow_finish(key, op, status="blocked")
            return revoked
        loan = session.borrow_explicit_with_claim(
            notice, account, admission=admission, claimed_receipt=receipt,
            confirmed=True, core_scope_current=current,
        )
        # A successful HTTP request is not sufficient. Only a verified
        # exact lender loan can mark the at-most-once checkpoint confirmed.
        if (not current() or type(loan) is not Loan
                or loan.identity_id != account.identity_id
                or loan.source_provider != account.provider_id
                or loan.source_credential != account.credential_id
                # Account aliases are validated against an explicitly
                # primary *empty-ID* account only. The provider is solely
                # responsible for attesting the authoritative borrower.
                or loan.source_account_id not in (
                    {account.unique_id, "primary"}
                    if account.unique_id == "" and account.is_primary is True
                    else {account.unique_id}
                )
                or loan.record_id != notice.record_id
                or loan.provider != notice.docbase
                or loan.medium != "digital"
                # Provider-native states may be numeric; do not invent an
                # active state from HTTP success or accept explicit closure.
                or loan.state in ("returned", "expired", "inactive")
                or type(loan.id) is not str or not loan.id):
            raise ValueError("unverified_lender_loan")
        finished = service.storage.loan_borrow_finish(
            key, op, status="confirmed", accepted=True,
            verified_loan_id=loan.id,
        )
        if finished is None:
            return _result("verification_unavailable", "borrow_checkpoint_unavailable")
        # A callback may revoke the requester while the confirmed receipt
        # is being written. Keep the durable fact, but do not publish its
        # private positive status to a context that no longer owns this click.
        if not current():
            return _result("verification_unavailable", "lender_scope_revoked")
        return _result("confirmed", "lender_loan_confirmed_transfer_unverified")
    except BorrowRequestNotSubmitted:
        # CAS release only when the provider *proved* that no mutation was
        # attempted. Never auto-retry in this call.
        try:
            released = service.storage.loan_borrow_release_unsent(key, op)
        except Exception:
            released = False
        return (_result("blocked", "borrow_not_submitted") if released else
                _result("verification_unavailable", "borrow_checkpoint_unavailable"))
    except Exception:
        # Timeout/unknown after dispatch may be a real supplier loan.
        # Preserve the consumed claim; never replay or disclose exception.
        try:
            service.storage.loan_borrow_finish(
                key, op, status="verification_unavailable",
            )
        except Exception:
            pass
        return _result("verification_unavailable", "borrow_result_unverified")


__all__ = ["execute_cross_profile_borrow"]
