"""#2888: one real user-requested loan refusal is an admission fact, not a failed GET.

The actual LibraryService wrapper and implementation are used with a synthetic
read-only eligibility decision. No HTTP, file acquisition, ADEPT, or provider
session is started by the test.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace as NS

import pytest

from cifereca.core.diagnostic_journal import query_journal
from cifereca.diagnostics import Diagnostics
from cifereca.plugin_api import Cancelled
from cifereca.logging_utils import CanonicalEventFormatter
from cifereca.providers import ProviderConfigurationError
from cifereca.services import LibraryService


def setup_service(code, *, level="normal"):
    service = object.__new__(LibraryService)
    service.diagnostics = Diagnostics(enabled=True, level=level)
    service.registry = NS(maybe_get=lambda name:
        NS(descriptor=NS(id="paris")) if name == "paris" else None
    )
    checks = []

    def eligibility(_loan):
        checks.append(code)
        return {"available": False, "code": code}

    service.loan_download_eligibility = eligibility
    loan = NS(
        source_provider="paris", source_credential="synthetic-only",
        medium="digital", id="private-loan-id", title="Synthetic loan",
    )
    return service, loan, checks


@pytest.mark.parametrize("code", (
    "not_digital", "provider_unavailable",
    "provider_disconnected", "download_unsupported",
))
def test_one_exact_refusal_from_existing_eligibility_at_admission(code, tmp_path):
    service, loan, checks = setup_service(code, level="errors")
    path = tmp_path / "cifereca.log"
    logger = logging.getLogger("cifereca")
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(CanonicalEventFormatter(
        "%(asctime)s %(levelname)s %(name)s [%(threadName)s] %(message)s"
    ))
    previous = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    try:
        # Reading button eligibility is not an attempted download and emits
        # no observation. The explicit command is the sole terminal emitter.
        assert service.can_download_loan(loan) is False
        assert service.diagnostics.snapshot() == []
        checks.clear()
        with pytest.raises(ProviderConfigurationError, match="Ce prêt ne permet pas"):
            service.reacquire_digital_loan(loan)
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)
        handler.close()

    assert checks == [code, code]  # admission + local explanation; zero provider GETs
    (row,) = service.diagnostics.snapshot()
    assert row["verified_loan_download"] is True
    assert row["category"] == "PNB"
    assert row["operation"] == "download_loan"
    assert row["detail"]["provider_id"] == "paris"
    assert row["detail"]["status"] == "refused"
    assert row["detail"]["code"] == code
    assert row["detail"]["phase"] == "admission"

    report = query_journal(path, provider_id="paris", severity="error")
    assert report["total"] == 1
    (event,) = report["events"]
    assert event["operation"] == "download_loan"
    assert event["status"] == "refused"
    assert event["detail"]["code"] == code
    assert event["detail"]["phase"] == "admission"
    assert event["provider_id"] == "paris"
    assert "private-loan-id" not in path.read_text(encoding="utf-8")


def test_unknown_broken_eligibility_code_stays_closed_without_crashing():
    service, loan, checks = setup_service("unexpected-future-code")
    with pytest.raises(ProviderConfigurationError):
        service.reacquire_digital_loan(loan)
    (row,) = service.diagnostics.snapshot()
    assert checks == ["unexpected-future-code", "unexpected-future-code"]
    assert row["detail"]["code"] == "unclassified_error"
    assert row["detail"]["status"] == "refused"


def test_later_provider_configuration_failure_is_not_falsely_labeled_refusal():
    service, loan, _checks = setup_service("provider_disconnected")
    def generic_failure(_loan):
        raise ProviderConfigurationError("unrelated provider setup failure")
    service._reacquire_digital_loan_impl = generic_failure
    with pytest.raises(ProviderConfigurationError, match="unrelated provider"):
        service.reacquire_digital_loan(loan)
    (row,) = service.diagnostics.snapshot()
    assert row["detail"]["status"] == "failed"
    assert row["detail"]["phase"] == "terminal"
    assert row["detail"]["code"] == "provider_failure"


def test_decision_state_change_does_not_claim_a_specific_provider_cause():
    service, loan, _checks = setup_service("provider_disconnected")
    service.can_download_loan = lambda _loan: False
    service.loan_download_eligibility = lambda _loan: {
        "available": True, "code": "available",
    }
    with pytest.raises(ProviderConfigurationError):
        service.reacquire_digital_loan(loan)
    (row,) = service.diagnostics.snapshot()
    assert row["detail"]["status"] == "refused"
    assert row["detail"]["phase"] == "admission"
    assert row["detail"]["code"] == "unclassified_error"


def test_broken_diagnostic_explanation_cannot_change_the_existing_refusal():
    service, loan, _checks = setup_service("provider_disconnected")
    service.can_download_loan = lambda _loan: False

    def broken_explanation(_loan):
        raise RuntimeError("Synthetic read-only diagnostic unavailable")

    service.loan_download_eligibility = broken_explanation
    with pytest.raises(ProviderConfigurationError, match="Ce prêt ne permet pas"):
        service.reacquire_digital_loan(loan)
    (row,) = service.diagnostics.snapshot()
    assert row["detail"]["status"] == "refused"
    assert row["detail"]["code"] == "unclassified_error"
    assert row["detail"]["phase"] == "admission"


@pytest.mark.parametrize("level", ("normal", "errors"))
def test_cooperative_explicit_cancellation_is_one_warning_not_provider_failure(
    level, tmp_path,
):
    service, loan, checks = setup_service("provider_disconnected", level=level)
    stopped = Cancelled("synthetic controlled interruption")
    requests = []

    def cooperative_cancel(_loan):
        requests.append("one-explicit-recovery")
        raise stopped

    # Skip provider acquisition entirely: the wrapper must classify the
    # actual cooperative exception without changing propagation or retrying.
    service._reacquire_digital_loan_impl = cooperative_cancel
    log_file = tmp_path / "cifereca.log"
    logger = logging.getLogger("cifereca")
    handler = logging.FileHandler(log_file, encoding="utf-8")
    handler.setFormatter(CanonicalEventFormatter(
        "%(asctime)s %(levelname)s %(name)s [%(threadName)s] %(message)s"
    ))
    previous = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    try:
        with pytest.raises(Cancelled) as raised:
            service.reacquire_digital_loan(loan)
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)
        handler.close()

    assert raised.value is stopped
    assert requests == ["one-explicit-recovery"]
    assert checks == []  # No eligibility read, second attempt or provider GET.
    (observed,) = service.diagnostics.snapshot()
    assert observed["verified_loan_download"] is True
    assert observed["detail"]["status"] == "cancelled"
    assert observed["detail"]["code"] == "operation_cancelled"
    assert observed["detail"]["phase"] == "terminal"
    assert observed["detail"]["provider_id"] == "paris"
    report = query_journal(log_file, provider_id="paris")
    assert report["total"] == 1
    (event,) = report["events"]
    assert event["severity"] == "warning"
    assert event["operation"] == "download_loan"
    assert event["status"] == "cancelled"
    assert event["detail"]["code"] == "operation_cancelled"
    assert query_journal(log_file, provider_id="paris", severity="error")["total"] == 0
    assert query_journal(log_file, provider_id="paris", severity="warning")["total"] == 1


@pytest.mark.parametrize("kind", ("provider_failure", "unexpected_failure", "attention"))
def test_explicit_failure_raw_trace_keeps_observed_loan_and_exception(
    kind, caplog,
):
    """A business code is not a replacement for the observed raw failure."""
    from cifereca.plugin_api import AttentionRequired
    service, loan, _checks = setup_service("provider_disconnected")
    if kind == "provider_failure":
        exc = ProviderConfigurationError("HTTP 403 on observed source path")
    elif kind == "unexpected_failure":
        exc = RuntimeError("original provider response was malformed")
    else:
        exc = AttentionRequired("original ADEPT rejection", code="adept_identity_unproven")
    def fail(_loan):
        raise exc
    service._reacquire_digital_loan_impl = fail
    with caplog.at_level(logging.WARNING, logger="cifereca"):
        with pytest.raises(type(exc)) as raised:
            service.reacquire_digital_loan(loan)
    assert raised.value is exc
    traces = [
        rec for rec in caplog.records
        if "TRACE explicit loan recovery" in rec.getMessage()
    ]
    assert len(traces) == 1
    observed = traces[0].getMessage()
    assert "provider=paris" in observed
    assert "loan_id=private-loan-id" in observed
    assert traces[0].exc_info[1] is exc
    (event,) = service.diagnostics.snapshot()
    assert event["detail"]["provider_id"] == "paris"
    assert event["detail"]["status"] in {"failed", "attention_required"}
    assert event["detail"]["phase"] == "terminal"
