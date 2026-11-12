"""P0 #5055: generic explicit-loan diagnostics do not infer BnF identity."""
from cifereca.core.diagnostic_explicit_loan_action import (
    LoanActionError, log_message, public_fields, public_log_fields,
)


def _event(provider="synthetic-pret", code="synthetic_access_denied"):
    return dict(
        schema_version=1, provider_id=provider, action="stage_loan_token",
        phase="provider_delivery", status="failed", reason_code=code,
        duration_ms=12,
    )


def test_synthetic_provider_roundtrip_and_closed_reason_schema():
    event = _event()
    assert public_fields(event) == event
    line = log_message(event)
    assert line.startswith("EVENT synthetic loan action ")
    assert public_log_fields(line) == event
    assert not public_fields(_event(code="bnf_access_denied"))
    assert not public_fields(_event(code="synthetic_https_secret"))
    assert not public_fields({**event, "secret": "private-url"})
    assert not public_log_fields(line.replace("synthetic loan", "bnf loan"))


def test_invalid_provider_and_malformed_event_fail_closed():
    assert public_fields(_event(provider="SYNTHETIC")) == {}
    assert public_fields(_event(provider="synthetic-pret", code="raw_error")) == {}
    assert LoanActionError("token=private").code == "loan_action_failed"
