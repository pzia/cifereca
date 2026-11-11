"""#5055: supplier BnF owns the explicit ACSM staging profile, never Core bytes."""
from types import SimpleNamespace as NS

from bnf_token_fixture import connected
from cifereca.core import explicit_loan_acsm
from cifereca.plugin_api import ProviderError
from cifereca_bnf_pret import _loan_stage_activity_reason


def test_bnf_profile_selects_only_attested_branch_and_closed_errors(monkeypatch):
    session, account, loan, state, calls, downloaded = connected()
    params = []

    def inert_generic_verifier(service, selected, **kwargs):
        assert service is stub_service
        assert selected is loan
        params.append(kwargs)
        return {"status": "candidate_acsm", "path": "/private/staged.acsm"}

    stub_service = NS()
    monkeypatch.setattr(
        explicit_loan_acsm, "stage_explicit_loan_acsm", inert_generic_verifier,
    )
    try:
        output = session.stage_loan_acsm_candidate(
            stub_service, loan, confirmed=True,
            expected_identity_id=account.identity_id,
            interprocess_lock=object(), lock_path=lambda: None,
            busy_error=RuntimeError, inspect_artifact=lambda _: None,
            observe_phase=lambda _: None,
        )
        assert output["status"] == "candidate_acsm"
        assert len(params) == 1
        profile = params[0]
        assert profile["expected_provider_id"] == "bnf-pret"
        assert profile["selected_branch"] == "bnf_html"
        assert profile["error_codes"] == {
            "confirmation": "bnf_explicit_confirmation_required",
            "scope": "bnf_loan_scope_unproven",
            "unavailable": "bnf_loan_token_unavailable",
            "invalid": "bnf_loan_token_invalid",
            "busy": "bnf_acquisition_busy",
        }
        assert profile["classify_activity_error"] is _loan_stage_activity_reason
        assert downloaded == []
    finally:
        session.close()


def test_provider_activity_error_is_closed_without_embedded_private_url():
    assert _loan_stage_activity_reason(
        ProviderError("access_denied"),
    ) == "bnf_activity_access_denied"
    secret = _loan_stage_activity_reason(
        ProviderError("private signed URL?token=sensitive"),
    )
    assert secret == "bnf_activity_unavailable"
    assert "sensitive" not in secret
