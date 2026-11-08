"""#2888: a completed BnF borrow callback is not a confirmed loan."""
from __future__ import annotations

import logging

import pytest

from cifereca.diagnostic_task_context import (
    diagnostic_task_business_result, diagnostic_task_reference,
    diagnostic_task_scope,
)
from cifereca.plugin_api import Cancelled
from tests.test_core_p0_981_redacted_task_traces import _runner


@pytest.mark.parametrize(("receipt", "expected", "message"), [
    ({"status": "blocked", "reason_code": "borrow_not_submitted",
      "provider_verified": False}, "blocked", "Emprunt bloqué — aucune confirmation"),
    ({"status": "verification_unavailable",
      "reason_code": "borrow_result_unverified",
      "provider_verified": False}, "pending",
     "Demande d’emprunt envoyée ; confirmation indisponible. "
     "Ne réempruntez pas : actualisez vos prêts ultérieurement."),
    ({"status": "submitted", "reason_code": "borrow_result_unverified",
      "provider_verified": False}, "pending",
     "Emprunt non confirmé — vérification nécessaire"),
    ({"status": "unsupported", "reason_code": "borrow_execution_unavailable",
      "provider_verified": False}, "blocked", "Emprunt bloqué — aucune confirmation"),
    ({"status": "failed", "reason_code": "borrow_result_unverified",
      "provider_verified": False}, "failed", "Emprunt non confirmé"),
    ({"status": "cancelled", "reason_code": "borrow_scope_changed",
      "provider_verified": False}, "cancelled", "Emprunt annulé"),
    ({"status": "confirmed", "reason_code": "borrow_active_loan_observed",
      "provider_verified": False}, "failed", "Emprunt non confirmé"),
    ({"status": "unknown", "reason_code": "PRIVATE_PASSWORD",
      "provider_verified": True}, "failed", "Emprunt non confirmé"),
])
def test_borrow_rejected_receipt_stays_business_failure(
    caplog, receipt, expected, message,
):
    runner, displayed, flush, logger = _runner()
    ok, failed = [], []
    try:
        with caplog.at_level(logging.INFO, logger=logger.name):
            runner.submit(
                "Demande de prêt", lambda: receipt,
                ok=ok.append, err=failed.append, kind="borrow",
                origin="user", success_toast="Emprunt confirmé",
            )
            flush()
        assert ok == [receipt]  # CoreUI still explains the actual refusal.
        assert not failed
        finished = [args for name, args in displayed if name == "finished"]
        assert len(finished) == 1
        assert finished[0][3:5] == (False, message)
        text = caplog.text
        assert f"business_status={expected}" in text
        assert "status=success" not in text
        assert "PRIVATE_" not in text
        if receipt["reason_code"] != "PRIVATE_PASSWORD":
            assert f"reason_code={receipt['reason_code']}" in text
        else:
            assert "reason_code=" not in text
    finally:
        runner.close()


@pytest.mark.parametrize("receipt", [
    {"status": "confirmed", "reason_code": "borrow_active_loan_observed",
     "provider_verified": True},
    ("legacy loan", "legacy refreshed loans"),
])
def test_confirmed_or_legacy_borrow_preserves_success(caplog, receipt):
    runner, displayed, flush, logger = _runner()
    try:
        with caplog.at_level(logging.INFO, logger=logger.name):
            runner.submit(
                "Demande", lambda: receipt, kind="borrow",
                origin="user",
            )
            flush()
        assert [args[3] for name, args in displayed if name == "finished"] == [True]
        assert "status=success" in caplog.text
    finally:
        runner.close()


def test_cancelled_exception_is_not_recorded_as_failed_business():
    runner, displayed, flush, _logger = _runner()
    errors = []
    try:
        def cancelled():
            raise Cancelled("stop")
        runner.submit("Borrow", cancelled, err=errors.append, kind="borrow")
        flush()
        assert len(errors) == 1 and isinstance(errors[0], Cancelled)
        finished = [args for name, args in displayed if name == "finished"]
        assert finished[0][3:5] == (False, "Emprunt annulé")
    finally:
        runner.close()


def test_worker_correlation_reuses_existing_task_ref_without_fabrication(caplog):
    log = logging.getLogger("cifereca")
    blocked = {
        "status": "blocked", "reason_code": "borrow_not_submitted",
        "provider_verified": False,
    }
    with caplog.at_level(logging.INFO, logger=log.name):
        with diagnostic_task_scope():
            ref = diagnostic_task_reference()
            diagnostic_task_business_result(blocked, operation_kind="borrow")
        assert f"task_ref={ref} status=failed" in caplog.text
        caplog.clear()
        with diagnostic_task_scope():
            ref = diagnostic_task_reference()
            diagnostic_task_business_result(blocked)
        assert f"task_ref={ref} status=success" in caplog.text
        caplog.clear()
        with diagnostic_task_scope():
            diagnostic_task_business_result(blocked, operation_kind="borrow")
        assert "TRACE task correlation" not in caplog.text
        caplog.clear()
        with diagnostic_task_scope():
            ref = diagnostic_task_reference()
            diagnostic_task_business_result({
                "status": "cancelled",
                "reason_code": "borrow_scope_changed",
                "provider_verified": False,
            }, operation_kind="borrow")
        assert f"task_ref={ref} status=cancelled" in caplog.text
