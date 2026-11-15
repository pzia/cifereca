"""P0 #5975: a staged ACSM is not a recovered book or an ADEPT import."""
from __future__ import annotations

import logging
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from tests.test_core_p0_981_redacted_task_traces import _runner


class HostileStatus(str):
    def __eq__(self, _other):
        raise AssertionError("do not compare untrusted status subclass")


class HostileDict(dict):
    def get(self, _key, _default=None):
        raise AssertionError("do not inspect a custom mapping")


@pytest.mark.parametrize(("kind", "result", "business", "message"), [
    ("loan-acquisition", {"status": "complete"}, "success", None),
    ("loan-acquisition", {"status": "attention_required", "code": "PRIVATE"},
     "pending", "Récupération du livre en attente"),
    ("loan-acquisition", {"status": "retryable_error", "code": "PRIVATE"},
     "failed", "Récupération du livre non confirmée"),
    ("loan-acquisition", {"status": "blocked"}, "blocked", "Récupération du livre non confirmée"),
    ("loan-acquisition", {"status": "candidate_acsm", "path": "/tmp/token.acsm"},
     "failed", "Récupération du livre non confirmée"),
    ("loan-acquisition", {"status": HostileStatus("complete")},
     "failed", "Récupération du livre non confirmée"),
    ("loan-acquisition", HostileDict(status="complete"),
     "failed", "Récupération du livre non confirmée"),
    ("loan-acquisition", True, "failed", "Récupération du livre non confirmée"),
    ("acsm-stage", {"status": "candidate_acsm", "path": "/private/token.acsm"},
     "success", None),
    ("acsm-stage", {"status": "candidate_acsm", "path": "/private/token.ACSM"},
     "success", None),
    ("acsm-stage", {"status": "attention_required", "code": "PRIVATE"},
     "pending", "Jeton ACSM en attente"),
    ("acsm-stage", {"status": "blocked", "code": "PRIVATE"},
     "blocked", "Jeton ACSM non confirmé"),
    ("acsm-stage", {"status": "candidate_acsm", "path": "/private/SECRET"},
     "failed", "Jeton ACSM non confirmé"),
    ("acsm-stage", {"status": "candidate_acsm", "path": "/private/a.acsm" + chr(0)},
     "failed", "Jeton ACSM non confirmé"),
    ("acsm-stage", {"status": "candidate_acsm", "path": "/private/a.acsm" + "a" * 5000},
     "failed", "Jeton ACSM non confirmé"),
    ("acsm-stage", {"status": "candidate_acsm", "path": HostileStatus("/private/a.acsm")},
     "failed", "Jeton ACSM non confirmé"),
    ("acsm-stage", {"status": HostileStatus("candidate_acsm"), "path": "/private/a.acsm"},
     "failed", "Jeton ACSM non confirmé"),
    ("acsm-stage", {"status": "complete"}, "failed", "Jeton ACSM non confirmé"),
    ("acsm-stage", None, "failed", "Jeton ACSM non confirmé"),
])
def test_distinct_exact_core_receipts_never_fake_download(
    kind, result, business, message, caplog,
):
    runner, displayed, flush, logger = _runner()
    observed, errors = [], []
    perf = Mock()
    runner.window.ui_perf = NS(record_phase=perf)
    try:
        with caplog.at_level(logging.INFO, logger=logger.name):
            runner.submit("Jeton privé", lambda: result, ok=observed.append,
                          err=errors.append, kind=kind, origin="user")
            flush()
        assert observed == [result]
        assert errors == []
        finished = [args for event, args in displayed if event == "finished"]
        assert len(finished) == 1
        assert finished[0][3] is (business == "success")
        assert finished[0][4] == message
        assert [x.kwargs["outcome"] for x in perf.call_args_list
                if x.args[1] == "worker"] == [
                    "success" if business == "success" else "failed",
                ]
        terminal = [row.getMessage() for row in caplog.records
                    if "TRACE task outcome id=" in row.getMessage()]
        assert len(terminal) == 1
        assert f"kind={kind} origin=user" in terminal[0]
        assert f"business_status={business}" in terminal[0]
        assert "PRIVATE" not in caplog.text
        assert "SECRET" not in caplog.text
    finally:
        runner.close()


def test_unrelated_kind_cannot_infer_new_book_from_acsm():
    runner, displayed, flush, _logger = _runner()
    try:
        runner.submit("Bibliothèque", lambda: {"status": "candidate_acsm"},
                      kind="library", origin="user")
        flush()
        assert [args[3] for event, args in displayed if event == "finished"] == [True]
    finally:
        runner.close()


def test_isolated_submit_adapter_never_mints_typed_acquisition_receipt():
    """Legacy AST-only submit() hosts cannot bypass a missing Core classifier."""
    from tests.test_core_ui_1132_task_runner_shutdown_race import (
        _submit, _runner as isolated_runner,
    )
    submit, pending = _submit()
    runner, window, events = isolated_runner()
    window._activity_result = Mock()
    window.show_error = Mock()

    class Finished:
        def result(self):
            return {"status": "complete"}
        def add_done_callback(self, callback):
            callback(self)

    runner.pool = NS(submit=lambda _priority, _fn: Finished())
    submit(runner, "Livre privé", lambda: {"status": "complete"},
           kind="loan-acquisition")
    for callback, args in pending:
        callback(*args)
    finished = [values for event, values in events if event == "finished"]
    assert len(finished) == 1
    assert finished[0][3] is False
    assert finished[0][4] == "Récupération du livre non confirmée"
