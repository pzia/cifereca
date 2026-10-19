"""P0 #1143: automatic-loan provider failure text stays outside SQLite/UI/TRACE.

No real account, network, ADEPT fulfillment, file deletion, or provider calls.
The actual Core facade and local SQLite ledger are exercised.
"""
from __future__ import annotations

import logging

import pytest

from tests.test_core_0977_adept_retry_guard import _service, _loan


PRIVATE = "https://signed.invalid/pnb/loan.acsm?auth=PRIVATE_ACSM_URL_2026"


class HostileProviderException(RuntimeError):
    def __str__(self):
        raise AssertionError("Do not stringify provider exception")

    def __repr__(self):
        raise AssertionError("Do not repr provider exception")


HostileProviderException.__name__ = "PRIVATE_ACSM_URL_2026"


@pytest.mark.parametrize(("error", "expected_code"), [
    (RuntimeError(PRIVATE), None),
    (HostileProviderException(PRIVATE), None),
    (OSError(PRIVATE), None),
    (RuntimeError("Quota dépassé ; auth=" + PRIVATE), "provider_quota_exceeded"),
])
def test_private_provider_error_is_never_persisted_or_returned(
    tmp_path, monkeypatch, caplog, error, expected_code,
):
    service = _service(tmp_path)
    loan = _loan()
    calls = []
    def fail(received, **options):
        calls.append((received, options))
        raise error
    monkeypatch.setattr(service, "acquire_digital_loan", fail)
    try:
        with caplog.at_level(logging.INFO, logger="cifereca"):
            result = service._acquire_active_loan_automatically(loan)
        assert result["status"] == "retryable_error"
        assert result["code"] == expected_code
        assert result["error"] == "Acquisition du prêt indisponible"
        assert calls == [(loan, {})]
        [saved] = service.storage.acquisitions(provider_id="paris")
        assert saved["status"] == "retryable_error"
        assert saved["error_code"] == "provider_acquisition_failed"
        assert saved["error_message"] == "Acquisition du prêt indisponible"
        assert saved["attention_code"] is None
        assert PRIVATE not in repr(result) + caplog.text
        assert "PRIVATE_ACSM_URL_2026" not in saved["error_code"] + saved["error_message"]
        assert service.storage.catalog_entries() == []
    finally:
        service.close()


def test_provider_failure_does_not_change_retryable_reconciliation(
    tmp_path, monkeypatch,
):
    service = _service(tmp_path)
    loan = _loan()
    requests = []
    def fail(received, **options):
        requests.append(received.id)
        raise RuntimeError(PRIVATE)
    monkeypatch.setattr(service, "acquire_digital_loan", fail)
    try:
        first = service._acquire_active_loan_automatically(loan)
        second = service._acquire_active_loan_automatically(loan)
        assert first["status"] == second["status"] == "retryable_error"
        assert first["error"] == second["error"] == "Acquisition du prêt indisponible"
        assert requests == [loan.id, loan.id]
        assert all(row["error_message"] == "Acquisition du prêt indisponible"
                   for row in service.storage.acquisitions(provider_id="paris"))
    finally:
        service.close()
