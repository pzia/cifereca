"""Core boundary for a deliberately requested BnF loan ACSM, not a new loan.

Never use the automatic acquisition path, infer a borrower from the selected
account, create entitlements, or start Adobe fulfillment. Only a Web loan
positively observed in the exact connected provider session can be fetched.
"""
from __future__ import annotations
from pathlib import Path

from ..models import Loan
from ..plugin_api import ProviderConfigurationError, ProviderError
from .diagnostic_bnf_loan_action import BnfLoanActionError, provider_reason
from .loan_action_scope import capture_loan_action_scope

from .loan_acsm_token_validation import (
    MAX_LOAN_TOKEN_BYTES as _MAX_BYTES,
    valid_loan_acsm_token as _valid_loan_token,
    discard_untrusted_staged_acsm as _discard_invalid_candidate,
)


def _refuse(code: str):
    raise BnfLoanActionError(code) from None


def stage_bnf_loan_acsm(
    service, loan: Loan, *, confirmed: bool, expected_identity_id: str,
    interprocess_lock, lock_path, busy_error, inspect_artifact,
    observe_phase=lambda _phase: None,
) -> dict[str, str]:
    """Stage an ACSM candidate only after caller's affirmative consent."""
    if confirmed is not True:
        _refuse("bnf_explicit_confirmation_required")
    if (type(loan) is not Loan or loan.source_provider != "bnf-pret"
            or loan.medium != "digital" or loan.state != "active"
            or type(loan.source_credential) is not str or not loan.source_credential
            or type(loan.source_account_id) is not str or not loan.source_account_id
            or type(loan.identity_id) is not str or not loan.identity_id
            or type(loan.id) is not str or not loan.id
            or type(loan.record_id) is not str or not loan.record_id
            or type(expected_identity_id) is not str
            or expected_identity_id != loan.identity_id):
        _refuse("bnf_loan_scope_unproven")

    def active_identity():
        return (service.current_identity is not None
                and service.current_identity.identity_id == expected_identity_id
                and service.storage.identity_for_connection(
                    loan.source_provider, loan.source_credential) == expected_identity_id)

    observe_phase('source_scope')
    if not active_identity():
        _refuse("bnf_loan_scope_unproven")
    try:
        with interprocess_lock(lock_path()):
            if not active_identity():
                _refuse("bnf_loan_scope_unproven")
            try:
                # No primary/legacy fallback: source_account_id was mandatory.
                account = service._account_for_loan(loan)
                session = service._session_for_source(
                    loan.source_provider, loan.source_credential)
                if (account.identity_id != expected_identity_id
                        or account.provider_id != "bnf-pret"
                        or account.credential_id != loan.source_credential
                        or account.unique_id != loan.source_account_id
                        # The Core lease below checks the exact connected
                        # session, credential and unique borrower from inert
                        # session memory. Never invoke provider properties in
                        # this preflight: even a getter may perform I/O.
                        ):
                    _refuse("bnf_loan_scope_unproven")
            except Exception:
                _refuse("bnf_loan_scope_unproven")

            # Consent is already explicit, and this BnF-private action is
            # deliberately NOT the publicly advertised loan.download_token
            # capability. Capture the exact Core account/session/epoch before
            # any staging or provider GET. A same-object in-place mutation
            # must not retain its former authority.
            lease = capture_loan_action_scope(
                service, loan, account, session,
                expected_identity_id=expected_identity_id,
            )
            if lease is None:
                _refuse("bnf_loan_scope_unproven")
            observe_phase('local_staging')
            destination = service._acquisition_work_dir(
                expected_identity_id, "bnf-pret", loan.id)
            observe_phase('provider_delivery')
            # A context change after confirmation (or while local staging
            # was being prepared) must deny the GET itself, not just its
            # resulting candidate.
            if not lease.current(service):
                _refuse("bnf_loan_scope_unproven")
            # The provider owns the private loan/offer interpretation and
            # signed GET. No legacy download_digital fallback is authorized.
            offer_snapshot = getattr(
                session, "rights_selected_delivery_offer_snapshot", None,
            )
            deliver_selected = getattr(
                session, "rights_selected_delivery_candidate", None,
            )
            if not callable(offer_snapshot) or not callable(deliver_selected):
                _refuse("bnf_loan_token_unavailable")
            phase = "activity"
            try:
                selected = offer_snapshot(
                    account, edition_id=loan.record_id, right_id=loan.id,
                    kind="loan", branch="bnf_html", explicit_user_action=True,
                )
                scope = selected.scope
                right = selected.exact(loan.record_id, loan.id, "loan")
                if (scope.identity_id != expected_identity_id
                        or scope.provider_id != loan.source_provider
                        or scope.credential_id != loan.source_credential
                        or scope.account_id != loan.source_account_id
                        or selected.branch != "bnf_html"
                        or right is None
                        or right.provider_attested is not True
                        or right.delivery_attested is not True
                        or type(right.delivery_offer_id) is not str
                        or not right.delivery_offer_id
                        or not lease.current(service)):
                    _refuse("bnf_loan_scope_unproven")
                phase = "candidate"
                artifact, url = deliver_selected(
                    account, edition_id=loan.record_id, right_id=loan.id,
                    kind="loan", branch=selected.branch,
                    delivery_offer_id=right.delivery_offer_id,
                    target_dir=destination, explicit_user_action=True,
                    admission_current=lambda: lease.current(service),
                )
            except BnfLoanActionError:
                raise
            except Exception as exc:
                # Distinguish private activity re-read from a signed token GET,
                # retaining the previously published closed diagnostic codes.
                # Never expose arbitrary provider exception text.
                if phase == "activity":
                    if (isinstance(exc, ProviderError)
                            and exc.args == ("bnf_selected_offer_scope_changed",)):
                        _refuse("bnf_loan_not_observed")
                    reason = provider_reason(exc)
                    if reason in {
                        "bnf_auth_required", "bnf_access_denied",
                        "bnf_rate_limited", "bnf_http_unavailable",
                        "bnf_transport_unavailable", "bnf_operation_timeout",
                    }:
                        _refuse("bnf_activity_" + reason[4:])
                    _refuse("bnf_activity_unavailable")
                _refuse(provider_reason(exc))
            observe_phase('artifact_validation')
            # Network I/O can switch the Core epoch even if the selected
            # identity later returns to its original value (ABA). Discard
            # only the private candidate in our own staging directory.
            if not lease.current(service):
                _discard_invalid_candidate(artifact, destination)
                _refuse("bnf_loan_scope_unproven")
            try:
                if not isinstance(artifact, Path) or type(url) is not str or url:
                    _refuse("bnf_loan_token_invalid")
                if (destination.is_symlink() or artifact.is_symlink()
                        or artifact.parent.resolve() != destination.resolve()
                        or artifact.suffix.casefold() != ".acsm"
                        or not artifact.is_file()
                        or not 0 < artifact.stat().st_size <= _MAX_BYTES
                        or not _valid_loan_token(artifact, inspect_artifact)):
                    _refuse("bnf_loan_token_invalid")
                if not lease.current(service):
                    _discard_invalid_candidate(artifact, destination)
                    _refuse("bnf_loan_scope_unproven")
            except (OSError, ValueError):
                _discard_invalid_candidate(artifact, destination)
                _refuse("bnf_loan_token_invalid")
            except ProviderConfigurationError:
                _discard_invalid_candidate(artifact, destination)
                raise
            # The borrower/session may have been replaced during HTTP.
            # A valid ACSM from a stale session must never be handed to ADEPT.
            observe_phase('source_revalidation')
            try:
                still_bound = lease.current(service)
            except Exception:
                still_bound = False
            if not still_bound:
                _discard_invalid_candidate(artifact, destination)
                _refuse("bnf_loan_scope_unproven")
            # The candidate is deliberately NOT an acquisition, a loan
            # fulfillment receipt, or a new/expired entitlement.
            return {"status": "candidate_acsm", "path": str(artifact)}
    except busy_error:
        _refuse("bnf_acquisition_busy")
