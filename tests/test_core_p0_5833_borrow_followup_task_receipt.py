"""P0 #5833: read-only uncertain-loan follow-up has an exact task receipt."""
from __future__ import annotations

import logging

import pytest

from tests.test_core_p0_981_redacted_task_traces import _runner

CONFIRMED = "borrow_verification_current_loan_observed"


class Hostile(str):
    def __eq__(self, _other):
        raise AssertionError("untrusted equality")


class HostileDict(dict):
    def get(self, _key, _default=None):
        raise AssertionError("untrusted mapping")


def receipt(status, reason):
    return {
        "status": status, "reason_code": reason,
        "retry_borrow": False, "mutated": False,
    }


@pytest.mark.parametrize(("result", "expected", "message"), [
    (receipt("observed_current_loan", CONFIRMED), "success", None),
    (receipt("unavailable", "borrow_verification_observation_partial"),
     "pending", "Prêt non encore vérifié — aucune nouvelle demande envoyée"),
    (receipt("unavailable", "borrow_verification_loan_unconfirmed"),
     "pending", "Prêt non encore vérifié — aucune nouvelle demande envoyée"),
    (receipt("blocked", "borrow_verification_scope_invalid"),
     "blocked", "Vérification du prêt bloquée"),
    (receipt("blocked", "/PRIVATE/Bearer SECRET"),
     "blocked", "Vérification du prêt bloquée"),
    (receipt("observed_current_loan", "other"),
     "failed", "Vérification du prêt non confirmée"),
    ({**receipt("observed_current_loan", CONFIRMED), "mutated": True},
     "failed", "Vérification du prêt non confirmée"),
    ({**receipt("observed_current_loan", CONFIRMED), "retry_borrow": True},
     "failed", "Vérification du prêt non confirmée"),
    ({**receipt("observed_current_loan", CONFIRMED), "status": Hostile("observed_current_loan")},
     "failed", "Vérification du prêt non confirmée"),
    ({**receipt("observed_current_loan", CONFIRMED), "reason_code": Hostile(CONFIRMED)},
     "failed", "Vérification du prêt non confirmée"),
    (HostileDict(receipt("observed_current_loan", CONFIRMED)),
     "failed", "Vérification du prêt non confirmée"),
    ({"status": "observed_current_loan"}, "failed", "Vérification du prêt non confirmée"),
    (None, "failed", "Vérification du prêt non confirmée"),
])
def test_followup_task_does_not_assert_success_without_verified_loan(
    caplog, result, expected, message,
):
    runner, events, flush, logger = _runner()
    observed, errors = [], []
    try:
        with caplog.at_level(logging.INFO, logger=logger.name):
            runner.submit("Vérification prêt", lambda: result, ok=observed.append,
                          err=errors.append, kind="borrow-verification", origin="user")
            flush()
        finished = [args for kind, args in events if kind == "finished"]
        assert len(finished) == 1
        assert finished[0][3] is (expected == "success")
        assert finished[0][4] == message
        assert observed == [result]
        assert errors == []
        lines = [record.getMessage() for record in caplog.records
                 if "TRACE task outcome id=" in record.getMessage()]
        assert len(lines) == 1
        assert "kind=borrow-verification origin=user" in lines[0]
        assert f"business_status={expected}" in lines[0]
        assert "SECRET" not in caplog.text and "PRIVATE" not in caplog.text
    finally:
        runner.close()


def test_followup_worker_error_never_reports_verified_loan(caplog):
    runner, events, flush, logger = _runner()
    errors = []

    def worker():
        raise RuntimeError("PRIVATE_BEARER")

    try:
        with caplog.at_level(logging.INFO, logger=logger.name):
            runner.submit("Vérification prêt", worker, err=errors.append,
                          kind="borrow-verification", origin="user")
            flush()
        assert len(errors) == 1
        assert [args[3] for kind, args in events if kind == "finished"] == [False]
        assert "PRIVATE_BEARER" not in caplog.text
    finally:
        runner.close()


def test_legacy_borrow_business_classification_is_unchanged():
    runner, events, flush, _logger = _runner()
    try:
        runner.submit("Prêt", lambda: {
            "status": "confirmed", "provider_verified": True,
        }, kind="borrow", origin="user")
        flush()
        assert [args[3] for kind, args in events if kind == "finished"] == [True]
    finally:
        runner.close()
