"""P0 #5975: a passive lender file check is not a new supplier loan."""
from __future__ import annotations

import logging
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from cifereca.core.borrow_for_identity_placement_resolution import (
    LenderLocalPlacement,
)
from tests.test_core_p0_981_redacted_task_traces import _runner


class HostileString(str):
    def __eq__(self, _other):
        raise AssertionError("custom equality must not run")


def placement(status="present", reason="lender_exact_local_placement_present",
              *, placement_id="PRIVATE-PLACEMENT", loan="confirmed",
              executable=False, retry_borrow=False):
    return LenderLocalPlacement(
        status, reason, _placement_id=placement_id, loan=loan,
        executable=executable, retry_borrow=retry_borrow,
    )


@pytest.mark.parametrize(("result", "business", "message"), [
    (placement(), "success", None),
    (placement("unavailable", "lender_delivery_unverified", placement_id=None),
     "pending", "Placement du prêteur en attente de livraison"),
    (placement("unavailable", "lender_scope_unverified",
               placement_id=None, loan="unknown"),
     "blocked", "Vérification du placement bloquée"),
    (placement("unavailable", "lender_loan_not_confirmed",
               placement_id=None, loan="not_attempted"),
     "blocked", "Vérification du placement bloquée"),
    (placement("unavailable", "lender_delivery_unverified",
               placement_id="PRIVATE-PLACEMENT"),
     "failed", "Placement du prêteur non confirmé"),
    (placement("unavailable", "lender_delivery_unverified",
               placement_id=None, loan="unknown"),
     "failed", "Placement du prêteur non confirmé"),
    (placement(placement_id=""), "failed", "Placement du prêteur non confirmé"),
    (placement(placement_id="A" * 2000), "failed", "Placement du prêteur non confirmé"),
    (placement(placement_id="A" + chr(0)), "failed", "Placement du prêteur non confirmé"),
    (placement(placement_id=HostileString("PRIVATE-PLACEMENT")),
     "failed", "Placement du prêteur non confirmé"),
    (placement(reason="unverified"), "failed", "Placement du prêteur non confirmé"),
    (placement(status=HostileString("present")), "failed", "Placement du prêteur non confirmé"),
    (placement(reason=HostileString("lender_exact_local_placement_present")),
     "failed", "Placement du prêteur non confirmé"),
    (placement(loan=HostileString("confirmed")),
     "failed", "Placement du prêteur non confirmé"),
    (placement(executable=True), "failed", "Placement du prêteur non confirmé"),
    (placement(retry_borrow=True), "failed", "Placement du prêteur non confirmé"),
    ({"status": "present", "_placement_id": "PRIVATE-PLACEMENT"},
     "failed", "Placement du prêteur non confirmé"),
    (None, "failed", "Placement du prêteur non confirmé"),
])
def test_exact_readonly_lender_placement_receipt(
    caplog, result, business, message,
):
    runner, displayed, flush, logger = _runner()
    observed, errors = [], []
    perf = Mock()
    runner.window.ui_perf = NS(record_phase=perf)
    try:
        with caplog.at_level(logging.INFO, logger=logger.name):
            runner.submit("Lecture locale", lambda: result,
                          kind="lender-placement-check", origin="user",
                          ok=observed.append, err=errors.append)
            flush()
        assert observed == [result] and errors == []
        finished = [args for event, args in displayed if event == "finished"]
        assert len(finished) == 1
        assert finished[0][3] is (business == "success")
        assert finished[0][4] == message
        worker = [call for call in perf.call_args_list if call.args[1] == "worker"]
        assert len(worker) == 1
        assert worker[0].kwargs["outcome"] == (
            "success" if business == "success" else "failed"
        )
        traces = [row.getMessage() for row in caplog.records
                  if "TRACE task outcome id=" in row.getMessage()]
        assert len(traces) == 1
        assert "kind=lender-placement-check origin=user" in traces[0]
        assert f"business_status={business}" in traces[0]
        assert "PRIVATE-PLACEMENT" not in caplog.text
    finally:
        runner.close()


def test_placement_check_exceptions_never_report_a_loan():
    runner, displayed, flush, _logger = _runner()
    errors = []
    def work():
        raise RuntimeError("PRIVATE-PLACEMENT")
    try:
        runner.submit("Vérification", work, kind="lender-placement-check",
                      origin="user", err=errors.append)
        flush()
        assert len(errors) == 1
        assert [args[3] for event, args in displayed if event == "finished"] == [False]
    finally:
        runner.close()
