"""#2888 explicit loan recovery: one terminal event, closed provider provenance.

All loans, providers and errors are synthetic. No URL, private loan ID,
download, ADEPT invocation or network request is involved.
"""
from __future__ import annotations

from types import SimpleNamespace as NS
import logging

import pytest

from cifereca.core.diagnostic_loan_download import (
    PREFIX, log_message, public_fields, public_log_fields,
)
from cifereca.diagnostics import Diagnostics
from cifereca.plugin_api import AttentionRequired
from cifereca.providers import ProviderConfigurationError
from cifereca.services import LibraryService


PRIVATE = "PRIVATE_ACCOUNT_PERSON@example.test/PASSWORD?token=SECRET"


def _loan(provider="paris"):
    return NS(source_provider=provider, source_credential=PRIVATE,
              id="LOAN_" + PRIVATE, title="PRIVATE_BOOK_TITLE")


def _service(*, provider="paris", outcome=None, error=None, level="normal"):
    service = object.__new__(LibraryService)
    service.diagnostics = Diagnostics(enabled=True, level=level)
    class Registry:
        def maybe_get(self, slug):
            return NS(descriptor=NS(id=slug)) if slug == provider else None
    service.registry = Registry()
    calls = []
    def execute(loan):
        calls.append(loan)
        if error is not None:
            raise error
        return outcome if outcome is not None else {"status": "complete"}
    service._reacquire_digital_loan_impl = execute
    return service, calls


@pytest.mark.parametrize("kind,outcome,error,status,code", [
    ("success", {"status": "complete"}, None, "success", "completed"),
    ("identity_block", None, AttentionRequired("PRIVATE", code="adept_identity_unproven"),
     "attention_required", "adept_identity_unproven"),
    ("busy", None, AttentionRequired("PRIVATE", code="acquisition_busy"),
     "attention_required", "acquisition_busy"),
    ("provider", None, ProviderConfigurationError("PRIVATE"), "failed", "provider_failure"),
    ("exception", None, RuntimeError(PRIVATE), "failed", "unclassified_error"),
    ("pending", {"status": "attention_required", "code": "adept_loan_identity_conflict"},
     None, "attention_required", "adept_loan_identity_conflict"),
    ("retryable", {"status": "retryable_error", "code": "acquisition_busy"},
     None, "retryable_error", "acquisition_busy"),
])
def test_exactly_one_attested_terminal_from_explicit_action_only(
    caplog, kind, outcome, error, status, code,
):
    service, calls = _service(outcome=outcome, error=error)
    with caplog.at_level(logging.INFO, logger="cifereca"):
        if error is None:
            assert service.reacquire_digital_loan(_loan()) == outcome
        else:
            with pytest.raises(type(error)):
                service.reacquire_digital_loan(_loan())
    assert len(calls) == 1
    records = service.diagnostics.snapshot()
    assert len(records) == 1
    event = records[0]
    assert event["category"] == "PNB"
    assert event["operation"] == "download_loan"
    assert event["verified_loan_download"] is True
    assert event["detail"]["provider_id"] == "paris"
    assert event["detail"]["code"] == code
    assert event["detail"]["status"] == status
    assert event["detail"]["operation_kind"] == "download_loan"
    assert event["detail"]["phase"] == "terminal"
    assert event["detail"]["schema_version"] == 1
    assert type(event["detail"]["duration_ms"]) is int
    assert 0 <= event["detail"]["duration_ms"] <= 86_400_000
    assert "loan_ref" not in event["detail"]
    assert "task_ref" not in event
    assert "verified_loan_download" in event
    text = caplog.text + repr(records)
    for secret in ("PRIVATE_", "PASSWORD", "SECRET", "LOAN_", "BOOK_TITLE", "example.test"):
        assert secret not in text
    messages = [r.getMessage() for r in caplog.records if r.getMessage().startswith(PREFIX)]
    assert len(messages) == 1
    assert public_log_fields(messages[0]) == event["detail"]


def test_unregistered_loan_provider_is_unknown_not_plugin_label():
    service, _ = _service(provider="paris")
    result = service.reacquire_digital_loan(_loan("fake-credential-PASSWORD"))
    assert result["status"] == "complete"
    assert service.diagnostics.snapshot()[0]["detail"]["provider_id"] == "source_inconnue"


def test_errors_only_preserves_failure_and_filters_success(caplog):
    good, _ = _service(level="errors")
    good.reacquire_digital_loan(_loan())
    assert good.diagnostics.snapshot() == []

    failing, _ = _service(
        level="errors", error=AttentionRequired("PRIVATE", code="adept_identity_unproven"),
    )
    with pytest.raises(AttentionRequired):
        failing.reacquire_digital_loan(_loan())
    assert len(failing.diagnostics.snapshot()) == 1


def test_generic_plugin_cannot_attest_provider_terminal():
    diagnostics = Diagnostics()
    diagnostics.add(
        "PNB", "download_loan", "failed",
        detail={"provider_id": "paris", "code": "provider_failure",
                "verified_loan_download": True},
    )
    row = diagnostics.snapshot()[0]
    assert "verified_loan_download" not in row
    assert "verified_loan_download" in row["detail"]  # untrusted text, not provenance


@pytest.mark.parametrize("broken", [
    {}, {"provider_id": PRIVATE},
    {"status": "hacked"}, {"duration_ms": True}, {"code": PRIVATE},
    {"phase": PRIVATE}, {"operation_kind": object()},
])
def test_closed_schema_rejects_hostile_fields_without_stringifying(broken):
    correct = {
        "schema_version": 1, "provider_id": "paris",
        "operation_kind": "download_loan",
        "phase": "terminal", "status": "success",
        "code": "completed", "duration_ms": 11,
    }
    candidate = dict(correct)
    candidate.update(broken)
    if not broken:
        candidate["private_url"] = PRIVATE
    assert public_fields(candidate) == {}
    assert log_message(candidate) is None


def test_parser_rejects_fake_log_codes_noncanonical_json_and_private_payload():
    allowed = public_fields({
        "schema_version": 1, "provider_id": "paris",
        "operation_kind": "download_loan", "phase": "terminal",
        "status": "success", "code": "completed", "duration_ms": 1,
    })
    canonical = log_message(allowed)
    assert public_log_fields(canonical) == allowed
    for line in (
        canonical + " " + PRIVATE,
        PREFIX + '{"provider_id":"paris","code":"completed"}',
        PREFIX + PRIVATE,
        PREFIX + '{"provider_id":"paris","status":"success","code":"provider_failure"}',
        "EVENT loan download " + str(allowed),
    ):
        assert public_log_fields(line) == {}


def test_failed_diagnostics_cannot_transform_a_real_success_or_error():
    service, _ = _service(outcome={"status": "complete"})
    service.diagnostics.add_core_loan_download = lambda *_: (_ for _ in ()).throw(
        RuntimeError(PRIVATE)
    )
    assert service.reacquire_digital_loan(_loan()) == {"status": "complete"}
    service._reacquire_digital_loan_impl = lambda _loan: (_ for _ in ()).throw(
        ProviderConfigurationError("PRIVATE")
    )
    with pytest.raises(ProviderConfigurationError, match="PRIVATE"):
        service.reacquire_digital_loan(_loan())
