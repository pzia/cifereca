"""#4987: one explicit cross-profile borrowing attempt, without profile switch.

A verified lender loan stays on the lender. Delivery, ADEPT and transfer are
separate operations with independent rights and MUST NOT be inferred here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from inspect import getattr_static
from threading import Lock
from uuid import uuid4

from ..models import Account
from ..plugin_api import LendingActionAdmission, BorrowRequestNotSubmitted
from ..plugin_api.lending_capabilities import assess_lending_operation
from ..plugin_api.loan_scope import loan_matches_account
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


@dataclass(frozen=True, repr=False)
class AdmittedCrossProfileBorrow:
    """Process-local confirmed action; no durable claim or provider I/O yet."""
    _service: object = field(repr=False)
    _work: object = field(repr=False)


def execute_admitted_cross_profile_borrow(service, admitted):
    if (type(admitted) is not AdmittedCrossProfileBorrow
            or admitted._service is not service or not callable(admitted._work)):
        return _result("blocked", "lender_scope_revoked")
    return admitted._work()


def prepare_cross_profile_borrow(
    service, *, captured_preview: BorrowForIdentityPreview,
    selected_option_id: str, expected_edition_id: str,
    observations: object, confirmed: bool = False, now: int | None = None,
    requesting_identity_id: str | None = None, scope_current=None,
    admit_at_confirmation: bool = False, reader_scope=None,
) -> AdmittedCrossProfileBorrow | dict[str, object]:
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
        if (type(requesting_identity_id) is not str
                or requesting_identity_id != captured_preview.requesting_identity_id
                or not callable(scope_current)
                or scope_current() is not True):
            return revoked
        source_epoch = service._source_context_epoch
        if reader_scope is not None:
            from .search_orchestration import SearchReadScope
            if (admit_at_confirmation is not True
                    or type(reader_scope) is not SearchReadScope
                    or reader_scope.identity_id != requesting_identity_id
                    or reader_scope.source_epoch != source_epoch
                    or not reader_scope.current(service)):
                return revoked
            context_epoch = reader_scope.source_epoch
        else:
            context_epoch = service._account_context_epoch
        if (type(context_epoch) is not int or context_epoch < 0
                or type(source_epoch) is not int or source_epoch < 0
                or scope_current() is not True):
            return revoked
        selection = revalidate_live_borrow_lender_selection(
            service, captured_preview=captured_preview,
            selected_option_id=selected_option_id,
            expected_edition_id=expected_edition_id,
            observations=observations, now=now,
            requesting_identity_id=requesting_identity_id,
            scope_current=scope_current,
            **({"reader_scope": reader_scope} if reader_scope is not None else {}),
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
            if scope_current() is not True:
                return revoked
            witness = _witness(
                service, row, context_epoch=context_epoch,
                source_epoch=source_epoch,
                now=now, expected_edition_id=expected_edition_id,
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
        # Only a confirmed, revalidated choice can obtain a source-only
        # execution lease. The requester registry, not the selected GTK
        # identity, identifies the original reader after admission.
        requester = service.identity_by_id(requesting_identity_id)
        lender = service.identity_by_id(account.identity_id)
        if requester is None or lender is None or not scope_current():
            return revoked

        def source_current():
            try:
                return (
                    service.identity_by_id(requesting_identity_id) is requester
                    and requester.identity_id == requesting_identity_id
                    and service._source_context_epoch == source_epoch
                    and (reader_scope is None or (
                        reader_scope.current(service)
                        and service.identity_by_id(account.identity_id) is lender
                    ))
                )
            except Exception:
                return False

        execution_scope = source_current if admit_at_confirmation else scope_current
        lease = capture_cross_profile_borrow_scope(
            service, requesting_identity_id=captured_preview.requesting_identity_id,
            borrowing_account=account, exact_notice=notice,
            lender_session=session, scope_current=execution_scope,
            admitted=admit_at_confirmation,
            **({"reader_scope": reader_scope} if reader_scope is not None else {}),
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
                return (execution_scope() is True
                        and lease.current(service)
                        and (admit_at_confirmation
                             or service._account_context_epoch == context_epoch)
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

    # The original menu must still be current when the consent is bound.
    # After this point GTK changes only affect publication.
    if not scope_current() or not current():
        return revoked
    attempt_lock = Lock()
    attempted = False

    def work():
        nonlocal attempted
        if reader_scope is not None:
            with attempt_lock:
                if attempted:
                    return _result("blocked", "borrow_confirmation_consumed")
                attempted = True
        return _execute_prepared(
            service, account=account, notice=notice, session=session,
            key=key, current=current,
        )

    return AdmittedCrossProfileBorrow(service, work)


def prepare_confirmed_borrow_for_scope(
    service, *, captured, selected_option_id: str, expected_edition_id: str,
    observations: object, confirmed: bool, now: int | None = None,
):
    """Admit one exact clicked option for the original explicit reader."""
    from .borrow_for_identity_live import ScopedBorrowPreview
    from .search_orchestration import SearchReadScope
    if confirmed is not True:
        return _result("blocked", "borrow_confirmation_required")
    if (type(captured) is not ScopedBorrowPreview
            or type(captured.reader_scope) is not SearchReadScope
            or not captured.reader_scope.current(service)):
        return _result("blocked", "lender_scope_revoked")
    scope = captured.reader_scope
    return prepare_cross_profile_borrow(
        service, captured_preview=captured.preview, selected_option_id=selected_option_id,
        expected_edition_id=expected_edition_id, observations=observations,
        confirmed=True, now=now, requesting_identity_id=scope.identity_id,
        scope_current=lambda: scope.current(service), admit_at_confirmation=True,
        reader_scope=scope,
    )


def _execute_prepared(service, *, account, notice, session, key, current):
    revoked = _result("blocked", "lender_scope_revoked")
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
        if (not current()
                or not loan_matches_account(loan, account, session.descriptor)
                or loan.record_id != notice.record_id
                or loan.provider != notice.docbase
                or loan.medium != "digital"
                # Provider-native states may be numeric; do not invent an
                # active state from HTTP success or accept explicit closure.
                # Some providers use numeric/unspecified states; preserve
                # their existing admission, but never treat an explicit
                # terminal, denied or pending textual state as an active loan.
                or (type(loan.state) is str and
                    loan.state.strip().casefold() in {
                        "returned", "expired", "inactive", "cancelled",
                        "canceled", "refused", "rejected", "failed",
                        "pending", "unknown", "closed",
                    })
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

def execute_cross_profile_borrow(
    service, *, captured_preview: BorrowForIdentityPreview,
    selected_option_id: str, expected_edition_id: str,
    observations: object, confirmed: bool = False, now: int | None = None,
    requesting_identity_id: str | None = None, scope_current=None,
) -> dict[str, object]:
    """Compatibility synchronous call; retains its navigation-scoped guard."""
    prepared = prepare_cross_profile_borrow(
        service, captured_preview=captured_preview,
        selected_option_id=selected_option_id,
        expected_edition_id=expected_edition_id, observations=observations,
        confirmed=confirmed, now=now, requesting_identity_id=requesting_identity_id,
        scope_current=scope_current,
    )
    if type(prepared) is not AdmittedCrossProfileBorrow:
        return prepared
    return execute_admitted_cross_profile_borrow(service, prepared)


__all__ = ["execute_cross_profile_borrow", "prepare_cross_profile_borrow",
           "AdmittedCrossProfileBorrow", "execute_admitted_cross_profile_borrow",
           "prepare_confirmed_borrow_for_scope"]
