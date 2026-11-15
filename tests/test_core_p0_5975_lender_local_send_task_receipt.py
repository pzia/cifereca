"""P0: a lender-local copy is not a new supplier borrow receipt."""
from __future__ import annotations

import logging

import pytest

from tests.test_core_p0_981_redacted_task_traces import _runner


class Hostile(str):
    def __eq__(self, _other):
        raise AssertionError("hostile equality")


def complete():
    return {
        "status": "complete", "loan": "confirmed", "delivery": "present",
        "transfer": "completed",
        "reason_code": "independent_local_copy_completed",
        "retry_borrow": False,
    }


@pytest.mark.parametrize(("receipt", "business", "message"), [
    (complete(), "success", None),
    ({"status": "blocked", "reason_code": "PRIVATE"},
     "blocked", "Copie du prêteur bloquée"),
    ({"status": "complete"}, "failed", "Copie du prêteur non confirmée"),
    ({**complete(), "loan": "not_attempted"}, "failed", "Copie du prêteur non confirmée"),
    ({**complete(), "retry_borrow": True}, "failed", "Copie du prêteur non confirmée"),
    ({**complete(), "delivery": "unavailable"}, "failed", "Copie du prêteur non confirmée"),
    ({**complete(), "transfer": "blocked"}, "failed", "Copie du prêteur non confirmée"),
    ({**complete(), "reason_code": "unknown"}, "failed", "Copie du prêteur non confirmée"),
    ({**complete(), "loan": Hostile("confirmed")}, "failed", "Copie du prêteur non confirmée"),
    ({**complete(), "delivery": Hostile("present")}, "failed", "Copie du prêteur non confirmée"),
    ({**complete(), "transfer": Hostile("completed")}, "failed", "Copie du prêteur non confirmée"),
    ({**complete(), "reason_code": Hostile("independent_local_copy_completed")},
     "failed", "Copie du prêteur non confirmée"),
    ({"status": Hostile("complete")}, "failed", "Copie du prêteur non confirmée"),
    (None, "failed", "Copie du prêteur non confirmée"),
])
def test_lender_local_copy_has_separate_strict_business_receipt(
    caplog, receipt, business, message,
):
    runner, displayed, flush, logger = _runner()
    results = []
    try:
        with caplog.at_level(logging.INFO, logger=logger.name):
            runner.submit("Copie du prêteur", lambda: receipt,
                          ok=results.append, kind="lender-local-send",
                          origin="user")
            flush()
        finished = [args for event, args in displayed if event == "finished"]
        assert len(finished) == 1
        assert finished[0][3] is (business == "success")
        assert finished[0][4] == message
        assert results == [receipt]
        traces = [rec.getMessage() for rec in caplog.records
                  if "TRACE task outcome id=" in rec.getMessage()]
        assert len(traces) == 1
        assert "kind=lender-local-send" in traces[0]
        assert f"business_status={business}" in traces[0]
        assert "PRIVATE" not in caplog.text
    finally:
        runner.close()


def test_supplier_borrow_kind_does_not_claim_lender_copy_receipt():
    runner, displayed, flush, _logger = _runner()
    try:
        runner.submit("Prêt fournisseur", complete, kind="borrow")
        flush()
        assert [args[3] for event, args in displayed if event == "finished"] == [False]
    finally:
        runner.close()
