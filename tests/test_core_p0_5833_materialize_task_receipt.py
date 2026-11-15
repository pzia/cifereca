"""P0 #5833: filesystem A→B materialization has its own honest task receipt."""
from __future__ import annotations

import logging

import pytest

from tests.test_core_p0_981_redacted_task_traces import _runner


class Hostile(str):
    def __eq__(self, _other):
        raise AssertionError("untrusted scalar equality")


class HostileDict(dict):
    def get(self, _key, _default=None):
        raise AssertionError("untrusted mapping")


def complete(status="complete"):
    return {
        "status": status, "target_identity_id": "bob",
        "placement_id": "new-placement", "path": "/PRIVATE/path",
    }


@pytest.mark.parametrize(("result", "success"), [
    (complete(), True),
    (complete("unchanged"), True),
    ({"status": "blocked", "private": "SECRET"}, False),
    ({"status": "complete"}, False),
    ({"status": "unchanged", "placement_id": "new-placement"}, False),
    ({**complete(), "target_identity_id": ""}, False),
    ({**complete(), "placement_id": ""}, False),
    ({**complete(), "path": ""}, False),
    ({**complete(), "path": "/tmp/" + chr(0) + ".epub"}, False),
    ({**complete(), "path": Hostile("/PRIVATE/path")}, False),
    ({**complete(), "status": Hostile("complete")}, False),
    ({**complete(), "target_identity_id": Hostile("bob")}, False),
    ({**complete(), "placement_id": Hostile("new-placement")}, False),
    (HostileDict(complete()), False),
    ("complete", False),
    (None, False),
])
def test_materialize_task_requires_exact_closed_core_receipt(
    caplog, result, success,
):
    runner, displayed, flush, logger = _runner()
    callbacks = []
    try:
        with caplog.at_level(logging.INFO, logger=logger.name):
            runner.submit("Envoi local", lambda: result, ok=callbacks.append,
                          kind="materialize", origin="user")
            flush()
        finished = [args for event, args in displayed if event == "finished"]
        assert len(finished) == 1
        assert finished[0][3] is success
        assert finished[0][4] == (
            None if success else "Envoi du fichier local non confirmé"
        )
        assert callbacks == [result]
        traces = [record.getMessage() for record in caplog.records
                  if "TRACE task outcome id=" in record.getMessage()]
        assert len(traces) == 1
        assert "kind=materialize origin=user" in traces[0]
        assert "business_status=" + ("success" if success else "failed") in traces[0]
        assert "SECRET" not in caplog.text
        assert "/PRIVATE/" not in caplog.text
    finally:
        runner.close()


def test_materialize_worker_failure_is_not_a_success(caplog):
    runner, displayed, flush, logger = _runner()
    errors = []

    def fail():
        raise FileNotFoundError("/PRIVATE/SECRET")

    try:
        with caplog.at_level(logging.INFO, logger=logger.name):
            runner.submit("Envoi local", fail, err=errors.append,
                          kind="materialize", origin="user")
            flush()
        assert len(errors) == 1
        assert [args[3] for event, args in displayed if event == "finished"] == [False]
        assert "SECRET" not in caplog.text
    finally:
        runner.close()
