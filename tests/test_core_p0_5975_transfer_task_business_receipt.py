"""P0 #5975/#5833: a normal-returning blocked local send is not a success.

The production TaskRunner is executed with its established headless GLib
adapter. No GTK, provider network, DRM or filesystem transfer is simulated.
"""
from __future__ import annotations

import logging
from unittest.mock import Mock

import pytest

from tests.test_core_p0_981_redacted_task_traces import _runner


def complete():
    return {
        "status": "complete", "loan": "not_attempted",
        "delivery": "present", "transfer": "completed",
        "reason_code": "independent_local_copy_completed",
        "retry_borrow": False, "copy": "created",
    }


def blocked():
    return {
        "status": "blocked", "loan": "not_attempted",
        "delivery": "unavailable", "transfer": "blocked",
        "reason_code": "local_send_context_revoked",
        "retry_borrow": False,
    }


class HostileStatus(str):
    def __eq__(self, _other):
        raise AssertionError("untrusted status equality")


class HostileDict(dict):
    def get(self, _key, _default=None):
        raise AssertionError("untrusted response mapping")


@pytest.mark.parametrize(("result", "status", "message"), [
    (complete(), "success", None),
    ({**complete(), "copy": "already_present"}, "success", None),
    (blocked(), "blocked", "Copie locale bloquée"),
    ({"status": "blocked"}, "blocked", "Copie locale bloquée"),
    ({**complete(), "copy": "missing"}, "failed", "Copie locale non confirmée"),
    ({**complete(), "loan": "confirmed"}, "failed", "Copie locale non confirmée"),
    ({**complete(), "retry_borrow": True}, "failed", "Copie locale non confirmée"),
    ({**complete(), "reason_code": "opaque"}, "failed", "Copie locale non confirmée"),
    ({"status": "retryable_error", "secret": "https://PRIVATE/?token=SECRET"},
     "failed", "Copie locale non confirmée"),
    ({"status": HostileStatus("complete")}, "failed", "Copie locale non confirmée"),
    ({**complete(), "loan": HostileStatus("not_attempted")},
     "failed", "Copie locale non confirmée"),
    ({**complete(), "delivery": HostileStatus("present")},
     "failed", "Copie locale non confirmée"),
    ({**complete(), "transfer": HostileStatus("completed")},
     "failed", "Copie locale non confirmée"),
    ({**complete(), "reason_code": HostileStatus("independent_local_copy_completed")},
     "failed", "Copie locale non confirmée"),
    ({**complete(), "copy": HostileStatus("created")},
     "failed", "Copie locale non confirmée"),
    (HostileDict(status="complete"), "failed", "Copie locale non confirmée"),
    (None, "failed", "Copie locale non confirmée"),
    (True, "failed", "Copie locale non confirmée"),
])
def test_exact_core_local_send_business_receipt_determines_task_status(
    caplog, result, status, message,
):
    runner, displayed, flush, logger = _runner()
    callbacks, exceptions = [], []
    reporter = Mock()
    runner.window._activity_result = reporter
    try:
        with caplog.at_level(logging.INFO, logger=logger.name):
            runner.submit(
                "Copie locale", lambda: result,
                ok=callbacks.append, err=exceptions.append,
                kind="transfer", origin="user",
                success_toast="Copie attestée",
            )
            flush()
        finishes = [args for name, args in displayed if name == "finished"]
        assert len(finishes) == 1
        assert finishes[0][3] is (status == "success")
        assert finishes[0][4] == message
        assert callbacks == [result]  # Business failure stays in the GTK success callback.
        assert exceptions == []
        if status == "success":
            reporter.assert_called_once()
        else:
            reporter.assert_not_called()
        terminals = [
            row.getMessage() for row in caplog.records
            if "TRACE task outcome id=" in row.getMessage()
        ]
        assert len(terminals) == 1
        assert f"kind=transfer origin=user status={'success' if status == 'success' else 'failed'}" in terminals[0]
        assert f"business_status={status}" in terminals[0]
        assert "SECRET" not in caplog.text
        assert "PRIVATE" not in caplog.text
    finally:
        runner.close()


def test_local_transfer_exception_remains_worker_failure_not_business_success():
    runner, displayed, flush, _logger = _runner()
    failures = []
    try:
        def fail():
            raise RuntimeError("https://private.example/?token=SECRET")
        runner.submit("Copie locale", fail, err=failures.append,
                      kind="transfer", origin="user")
        flush()
        assert len(failures) == 1
        finish = [args for name, args in displayed if name == "finished"]
        assert len(finish) == 1 and finish[0][3] is False
        assert finish[0][4] == "Copie locale non confirmée"
    finally:
        runner.close()


def test_unrelated_task_kind_does_not_infer_transfer_from_arbitrary_mapping():
    runner, displayed, flush, _logger = _runner()
    try:
        runner.submit("Legacy", lambda: blocked(), kind="library", origin="user")
        flush()
        assert [args[3] for name, args in displayed if name == "finished"] == [True]
    finally:
        runner.close()
