"""BnF public compatibility facade for the provider-neutral ACSM stage.

The provider owns its private loan activity/GET and stable BnF diagnostics.
The neutral Core owns the exact lease, lock, staging and candidate admission.
"""
from __future__ import annotations

from ..models import Loan
from ..plugin_api import ProviderError
from .diagnostic_bnf_loan_action import BnfLoanActionError, provider_reason
from .explicit_loan_acsm import stage_explicit_loan_acsm


def _activity_reason(exc: Exception) -> str:
    if (isinstance(exc, ProviderError)
            and exc.args == ("bnf_selected_offer_scope_changed",)):
        return "bnf_loan_not_observed"
    reason = provider_reason(exc)
    if reason in {
        "bnf_auth_required", "bnf_access_denied", "bnf_rate_limited",
        "bnf_http_unavailable", "bnf_transport_unavailable",
        "bnf_operation_timeout",
    }:
        return "bnf_activity_" + reason[4:]
    return "bnf_activity_unavailable"


def stage_bnf_loan_acsm(
    service, loan: Loan, *, confirmed: bool, expected_identity_id: str,
    interprocess_lock, lock_path, busy_error, inspect_artifact,
    observe_phase=lambda _phase: None,
) -> dict[str, str]:
    """Stage an exact observed BnF loan candidate without fulfilling ADEPT."""
    return stage_explicit_loan_acsm(
        service, loan, confirmed=confirmed,
        expected_identity_id=expected_identity_id,
        interprocess_lock=interprocess_lock, lock_path=lock_path,
        busy_error=busy_error, inspect_artifact=inspect_artifact,
        expected_provider_id="bnf-pret", selected_branch="bnf_html",
        action_error_type=BnfLoanActionError,
        error_codes={
            "confirmation": "bnf_explicit_confirmation_required",
            "scope": "bnf_loan_scope_unproven",
            "unavailable": "bnf_loan_token_unavailable",
            "invalid": "bnf_loan_token_invalid",
            "busy": "bnf_acquisition_busy",
        },
        classify_activity_error=_activity_reason,
        classify_candidate_error=provider_reason,
        observe_phase=observe_phase,
    )


__all__ = ["stage_bnf_loan_acsm"]
