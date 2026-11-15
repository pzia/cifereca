"""P0 #5975: worker telemetry and GTK receipt agree on business outcomes."""
from __future__ import annotations

from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from tests.test_core_p0_981_redacted_task_traces import _runner


def transfer():
    return {
        "status": "complete", "loan": "not_attempted", "delivery": "present",
        "transfer": "completed", "reason_code": "independent_local_copy_completed",
        "retry_borrow": False, "copy": "created",
    }


def lender_send():
    return {
        "status": "complete", "loan": "confirmed", "delivery": "present",
        "transfer": "completed", "reason_code": "independent_local_copy_completed",
        "retry_borrow": False,
    }


def followup():
    return {
        "status": "observed_current_loan",
        "reason_code": "borrow_verification_current_loan_observed",
        "retry_borrow": False, "mutated": False,
    }


@pytest.mark.parametrize(("kind", "value", "expected"), [
    ("transfer", transfer(), "success"),
    ("transfer", {"status": "blocked"}, "failed"),
    ("transfer", {"status": "complete", "copy": "missing"}, "failed"),
    ("acsm-import", {"status": "attention_required", "reason_code": "drm_required"}, "failed"),
    ("acsm-import", None, "failed"),
    ("lender-local-send", lender_send(), "success"),
    ("lender-local-send", {"status": "blocked"}, "failed"),
    ("lender-local-send", {"status": "complete"}, "failed"),
    ("borrow-verification", followup(), "success"),
    ("borrow-verification", {"status": "unavailable", "reason_code": "observation_partial",
                             "retry_borrow": False, "mutated": False}, "failed"),
    ("borrow-verification", {"status": "blocked", "reason_code": "scope_revoked",
                             "retry_borrow": False, "mutated": False}, "failed"),
    ("materialize", {"status": "blocked"}, "failed"),
    ("borrow", {"status": "confirmed", "provider_verified": True}, "success"),
    ("library", {"status": "blocked"}, "success"),
])
def test_worker_phase_never_reports_business_failure_as_success(kind, value, expected):
    runner, displayed, flush, _logger = _runner()
    observer = Mock()
    runner.window.ui_perf = NS(record_phase=observer)
    try:
        runner.submit("Private label not logged", lambda: value, kind=kind, origin="user")
        flush()
        worker = [
            call for call in observer.call_args_list
            if call.args[1] == "worker"
        ]
        assert len(worker) == 1
        assert worker[0].kwargs["outcome"] == expected
        finish = [args for name, args in displayed if name == "finished"]
        assert len(finish) == 1
        assert finish[0][3] is (expected == "success")
    finally:
        runner.close()


def test_worker_exception_keeps_failed_telemetry_and_callback():
    runner, displayed, flush, _logger = _runner()
    observer = Mock()
    runner.window.ui_perf = NS(record_phase=observer)
    errors = []

    def fail():
        raise RuntimeError("PRIVATE_BEARER")

    try:
        runner.submit("Copie", fail, kind="lender-local-send", origin="user",
                      err=errors.append)
        flush()
        assert len(errors) == 1
        worker = [call for call in observer.call_args_list if call.args[1] == "worker"]
        assert len(worker) == 1 and worker[0].kwargs["outcome"] == "failed"
        assert [args[3] for name, args in displayed if name == "finished"] == [False]
    finally:
        runner.close()
