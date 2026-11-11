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
        candidate_method = getattr_static(
            type(session), "loan_stage_candidate_reason",
        )
        assert isfunction(candidate_method)
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


def test_candidate_reason_is_provider_owned_closed_and_never_leaks_secrets():
    session, _account, _loan, _state, _calls, downloaded = connected()
    try:
        for reason in ("access_denied", "invalid_loan_token",
                       "download_redirect_refused", "activity_url_refused"):
            assert session.loan_stage_candidate_reason(
                ProviderError(reason),
            ) == "bnf_" + reason
        for error in (
            ProviderError("private signed URL?token=SECRET"),
            ValueError("access_denied"),
            ProviderError("BNF_UNKNOWN_REASON"),
        ):
            code = session.loan_stage_candidate_reason(error)
            assert code == "bnf_loan_token_unavailable"
            assert "SECRET" not in code
        assert downloaded == []
    finally:
        session.close()
