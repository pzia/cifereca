"""#5055: BnF policy is static provider data, Core owns all ACSM staging."""
from inspect import getattr_static, isfunction

from bnf_token_fixture import connected
from cifereca.plugin_api import ProviderError
from cifereca_bnf_pret import BnfProviderSession


def test_bnf_stage_profile_is_static_closed_data_without_remote_calls():
    session, account, loan, _state, calls, downloaded = connected()
    try:
        profile = getattr_static(type(session), "loan_acsm_stage_profile")
        assert type(profile) is tuple and len(profile) == 2
        assert profile[0] == "bnf_html"
        assert profile[1] == (
            "bnf_explicit_confirmation_required",
            "bnf_loan_scope_unproven",
            "bnf_loan_token_unavailable",
            "bnf_loan_token_invalid",
            "bnf_acquisition_busy",
        )
        reason_method = getattr_static(
            type(session), "loan_stage_activity_reason",
        )
        assert isfunction(reason_method)
        assert downloaded == []
    finally:
        session.close()


def test_provider_activity_reason_is_closed_without_private_url():
    session, _account, _loan, _state, _calls, downloaded = connected()
    try:
        assert session.loan_stage_activity_reason(
            ProviderError("access_denied"),
        ) == "bnf_activity_access_denied"
        assert session.loan_stage_activity_reason(
            ProviderError("bnf_selected_offer_scope_changed"),
        ) == "bnf_loan_not_observed"
        secret = session.loan_stage_activity_reason(
            ProviderError("private signed URL?token=sensitive"),
        )
        assert secret == "bnf_activity_unavailable"
        assert "sensitive" not in secret
        assert downloaded == []
    finally:
        session.close()
