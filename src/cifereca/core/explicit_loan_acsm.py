"""Core boundary for explicit, provider-selected ACSM staging.

No catalogue right, local file, session label or loan URL authorizes a GET.
The caller owns explicit consent and provider diagnostics. Core owns the
acquisition lock, exact identity/session lease and private file admission.
A staged ACSM is never CompletedDelivery, Artifact or entitlement.
"""
from __future__ import annotations

from pathlib import Path
from ..models import Loan
from ..plugin_api import ProviderConfigurationError
from .loan_action_scope import capture_loan_action_scope
from .selected_offer_scope import selected_offer_scope_matches
from .loan_acsm_token_validation import (
    MAX_LOAN_TOKEN_BYTES as _MAX_BYTES,
    valid_loan_acsm_token as _valid_loan_token,
    discard_untrusted_staged_acsm as _discard_invalid_candidate,
)


def stage_explicit_loan_acsm(
    service, loan: Loan, *, confirmed: bool, expected_identity_id: str,
    interprocess_lock, lock_path, busy_error, inspect_artifact,
    expected_provider_id: str, selected_branch: str,
    action_error_type, error_codes: dict[str, str],
    classify_activity_error, classify_candidate_error,
    observe_phase=lambda _phase: None,
) -> dict[str, str]:
    """Stage a provider-selected loan ACSM after exact explicit consent."""
    def _refuse(code: str):
        raise action_error_type(code) from None
    if confirmed is not True:
        _refuse(error_codes["confirmation"])
    if (type(loan) is not Loan or loan.source_provider != expected_provider_id
            or loan.medium != "digital" or loan.state != "active"
            or type(loan.source_credential) is not str or not loan.source_credential
            or type(loan.source_account_id) is not str or not loan.source_account_id
            or type(loan.identity_id) is not str or not loan.identity_id
            or type(loan.id) is not str or not loan.id
            or type(loan.record_id) is not str or not loan.record_id
            or type(expected_identity_id) is not str
            or expected_identity_id != loan.identity_id):
        _refuse(error_codes["scope"])

    def active_identity():
        return (service.current_identity is not None
                and service.current_identity.identity_id == expected_identity_id
                and service.storage.identity_for_connection(
                    loan.source_provider, loan.source_credential) == expected_identity_id)

    observe_phase('source_scope')
    if not active_identity():
        _refuse(error_codes["scope"])
    try:
        with interprocess_lock(lock_path()):
            if not active_identity():
                _refuse(error_codes["scope"])
            try:
                # No primary/legacy fallback: source_account_id was mandatory.
                account = service._account_for_loan(loan)
                session = service._session_for_source(
                    loan.source_provider, loan.source_credential)
                if (account.identity_id != expected_identity_id
                        or account.provider_id != expected_provider_id
                        or account.credential_id != loan.source_credential
                        or account.unique_id != loan.source_account_id
                        # The Core lease below checks the exact connected
                        # session, credential and unique borrower from inert
                        # session memory. Never invoke provider properties in
                        # this preflight: even a getter may perform I/O.
                        ):
                    _refuse(error_codes["scope"])
            except Exception:
                _refuse(error_codes["scope"])

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
                _refuse(error_codes["scope"])
            observe_phase('local_staging')
            destination = service._acquisition_work_dir(
                expected_identity_id, expected_provider_id, loan.id)
            observe_phase('provider_delivery')
            # A context change after confirmation (or while local staging
            # was being prepared) must deny the GET itself, not just its
            # resulting candidate.
            if not lease.current(service):
                _refuse(error_codes["scope"])
            # The provider owns the private loan/offer interpretation and
            # signed GET. No legacy download_digital fallback is authorized.
            offer_snapshot = getattr(
                session, "rights_selected_delivery_offer_snapshot", None,
            )
            deliver_selected = getattr(
                session, "rights_selected_delivery_candidate", None,
            )
            if not callable(offer_snapshot) or not callable(deliver_selected):
                _refuse(error_codes["unavailable"])
            phase = "activity"
            try:
                selected = offer_snapshot(
                    account, edition_id=loan.record_id, right_id=loan.id,
                    kind="loan", branch=selected_branch, explicit_user_action=True,
                )
                right = selected.exact(loan.record_id, loan.id, "loan")
                if (not selected_offer_scope_matches(
                        selected, identity_id=expected_identity_id,
                        provider_id=loan.source_provider,
                        credential_id=loan.source_credential,
                        account_id=loan.source_account_id, branch=selected_branch,
                    )
                        or right is None
                        or right.provider_attested is not True
                        or right.delivery_attested is not True
                        or type(right.delivery_offer_id) is not str
                        or not right.delivery_offer_id
                        or not lease.current(service)):
                    _refuse(error_codes["scope"])
                phase = "candidate"
                artifact, url = deliver_selected(
                    account, edition_id=loan.record_id, right_id=loan.id,
                    kind="loan", branch=selected.branch,
                    delivery_offer_id=right.delivery_offer_id,
                    target_dir=destination, explicit_user_action=True,
                    admission_current=lambda: lease.current(service),
                )
            except action_error_type:
                raise
            except Exception as exc:
                # Provider-specific reason mapping belongs to the adapter.
                # The generic Core never serializes an arbitrary exception.
                if phase == "activity":
                    _refuse(classify_activity_error(exc))
                _refuse(classify_candidate_error(exc))
            observe_phase('artifact_validation')
            # Network I/O can switch the Core epoch even if the selected
            # identity later returns to its original value (ABA). Discard
            # only the private candidate in our own staging directory.
            if not lease.current(service):
                _discard_invalid_candidate(artifact, destination)
                _refuse(error_codes["scope"])
            try:
                if not isinstance(artifact, Path) or type(url) is not str or url:
                    _refuse(error_codes["invalid"])
                if (destination.is_symlink() or artifact.is_symlink()
                        or artifact.parent.resolve() != destination.resolve()
                        or artifact.suffix.casefold() != ".acsm"
                        or not artifact.is_file()
                        or not 0 < artifact.stat().st_size <= _MAX_BYTES
                        or not _valid_loan_token(artifact, inspect_artifact)):
                    _refuse(error_codes["invalid"])
                if not lease.current(service):
                    _discard_invalid_candidate(artifact, destination)
                    _refuse(error_codes["scope"])
            except (OSError, ValueError):
                _discard_invalid_candidate(artifact, destination)
                _refuse(error_codes["invalid"])
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
                _refuse(error_codes["scope"])
            # The candidate is deliberately NOT an acquisition, a loan
            # fulfillment receipt, or a new/expired entitlement.
            return {"status": "candidate_acsm", "path": str(artifact)}
    except busy_error:
        _refuse(error_codes["busy"])
