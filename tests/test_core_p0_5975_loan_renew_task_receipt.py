"""P0: HTTP acceptance of RenewLoans is pending, never a verified renewal."""
from __future__ import annotations

import logging

import pytest

from tests.test_core_p0_981_redacted_task_traces import _runner


class Hostile(str):
    def __eq__(self, _other):
        raise AssertionError("untrusted supplier value")


class HostileDict(dict):
    def get(self, _key, _default=None):
        raise AssertionError("untrusted mapping")


def receipt(status, code, *, accepted=False, verified=False, retry=None):
    return {
        "status": status, "code": code,
        "provider_request_accepted": accepted,
        "provider_verified": verified,
        "retry": retry or (
            "verification_only" if status in ("submitted", "verification_unavailable")
            else "explicit_only"
        ),
    }


@pytest.mark.parametrize(("outcome", "expected", "message"), [
    (receipt("submitted", "renewal_provider_submitted", accepted=True),
     "pending", "Prolongation transmise — échéance non vérifiée"),
    (receipt("submitted", "renewal_previous_submission", accepted=True),
     "pending", "Prolongation transmise — échéance non vérifiée"),
    (receipt("verification_unavailable", "renewal_provider_response_unverified"),
     "pending", "Prolongation transmise — échéance non vérifiée"),
    (receipt("verification_unavailable", "loan_scope_changed", accepted=True),
     "pending", "Prolongation transmise — échéance non vérifiée"),
    (receipt("blocked", "loan_permission_unverified"),
     "blocked", "Demande de prolongation bloquée"),
    (receipt("blocked", "renewal_confirmation_required"),
     "blocked", "Demande de prolongation bloquée"),
    (receipt("blocked", "loan_action_busy"),
     "blocked", "Demande de prolongation bloquée"),
    (receipt("submitted", "renewal_provider_submitted", accepted=False),
     "failed", "Prolongation non confirmée"),
    (receipt("submitted", "renewal_provider_submitted", accepted=True, verified=True),
     "failed", "Prolongation non confirmée"),
    (receipt("complete", "renewal_provider_submitted", accepted=True),
     "failed", "Prolongation non confirmée"),
    (receipt("blocked", "SECRET_URL"),
     "failed", "Prolongation non confirmée"),
    ({**receipt("submitted", "renewal_provider_submitted", accepted=True),
      "code": Hostile("renewal_provider_submitted")},
     "failed", "Prolongation non confirmée"),
    ({**receipt("submitted", "renewal_provider_submitted", accepted=True),
      "status": Hostile("submitted")},
     "failed", "Prolongation non confirmée"),
    ({**receipt("submitted", "renewal_provider_submitted", accepted=True),
      "retry": Hostile("verification_only")},
     "failed", "Prolongation non confirmée"),
    ({**receipt("submitted", "renewal_provider_submitted", accepted=True),
      "provider_request_accepted": 1},
     "failed", "Prolongation non confirmée"),
    (HostileDict(receipt("submitted", "renewal_provider_submitted", accepted=True)),
     "failed", "Prolongation non confirmée"),
    ({"status": "submitted", "private": "https://PRIVATE?token=SECRET"},
     "failed", "Prolongation non confirmée"),
    (None, "failed", "Prolongation non confirmée"),
])
def test_renew_task_never_confuses_submitted_with_renewed(
    caplog, outcome, expected, message,
):
    runner, events, flush, logger = _runner()
    results = []
    try:
        with caplog.at_level(logging.INFO, logger=logger.name):
            runner.submit("Demande prolongation", lambda: outcome,
                          ok=results.append, kind="loan-renew", origin="user")
            flush()
        finished = [args for event, args in events if event == "finished"]
        assert len(finished) == 1
        assert finished[0][3] is False  # HTTP acknowledgement is not proof.
        assert finished[0][4] == message
        assert results == [outcome]  # Existing GTK feedback still gets Core result.
        traces = [rec.getMessage() for rec in caplog.records
                  if "TRACE task outcome id=" in rec.getMessage()]
        assert len(traces) == 1
        assert "kind=loan-renew origin=user" in traces[0]
        assert f"business_status={expected}" in traces[0]
        assert "SECRET" not in caplog.text and "PRIVATE" not in caplog.text
    finally:
        runner.close()


def test_renew_worker_exception_does_not_escape_trace(caplog):
    runner, events, flush, logger = _runner()
    failures = []

    def broken():
        raise RuntimeError("SECRET_BEARER")

    try:
        with caplog.at_level(logging.INFO, logger=logger.name):
            runner.submit("Demande prolongation", broken, err=failures.append,
                          kind="loan-renew", origin="user")
            flush()
        assert len(failures) == 1
        assert [args[3] for event, args in events if event == "finished"] == [False]
        assert "SECRET_BEARER" not in caplog.text
    finally:
        runner.close()


def test_legacy_borrow_still_can_return_an_attested_loan_success():
    runner, events, flush, _logger = _runner()
    try:
        runner.submit("Borrow", lambda: {
            "status": "confirmed", "provider_verified": True,
        }, kind="borrow")
        flush()
        assert [args[3] for event, args in events if event == "finished"] == [True]
    finally:
        runner.close()


def test_isolated_legacy_submit_cannot_claim_a_renewal_without_classifier():
    from types import SimpleNamespace as NS
    from tests.test_core_ui_1132_task_runner_shutdown_race import (
        _submit, _runner as isolated_runner,
    )
    submit, pending = _submit()
    runner, window, events = isolated_runner()
    window._activity_result = lambda *_a, **_kw: None
    class Finished:
        def result(self):
            return receipt("submitted", "renewal_provider_submitted", accepted=True)
        def add_done_callback(self, fn):
            fn(self)
    runner.pool = NS(submit=lambda _priority, _fn: Finished())
    submit(runner, "Prolongation", lambda: None, kind="loan-renew")
    for callback, args in pending:
        callback(*args)
    terminal = [values for name, values in events if name == "finished"]
    assert len(terminal) == 1
    assert terminal[0][3] is False
    assert terminal[0][4] == "Prolongation non confirmée"
