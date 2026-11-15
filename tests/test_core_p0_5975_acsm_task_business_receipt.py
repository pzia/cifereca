"""P0: manual ACSM staging/attention is not an imported artifact receipt."""
from __future__ import annotations

import logging

import pytest

from tests.test_core_p0_981_redacted_task_traces import _runner

ACQ = "manual-acsm:" + "a" * 32


class HostileStatus(str):
    def __eq__(self, other):
        raise AssertionError("untrusted equality")


class HostileResult(dict):
    def get(self, key, default=None):
        raise AssertionError("untrusted mapping")


@pytest.mark.parametrize(("receipt", "expected", "message"), [
    ({"status": "complete", "path": "/tmp/book.epub",
      "acquisition_id": ACQ}, "success", None),
    ({"status": "unchanged", "path": "/tmp/book.epub",
      "acquisition_id": ACQ}, "success", None),
    ({"status": "attention_required", "code": "adept_profile_required",
      "staging": "/PRIVATE/token=SECRET"}, "pending",
     "Import ACSM en attente — configuration DRM nécessaire"),
    ({"status": "attention_required", "code": "PRIVATE_TOKEN"}, "pending",
     "Import ACSM en attente — configuration DRM nécessaire"),
    ({"status": "complete"}, "failed", "Import ACSM non confirmé"),
    ({"status": "unchanged", "path": "/tmp/book.epub"},
     "failed", "Import ACSM non confirmé"),
    ({"status": "complete", "path": HostileStatus("/tmp/book.epub"),
      "acquisition_id": ACQ}, "failed", "Import ACSM non confirmé"),
    ({"status": "complete", "path": "/tmp/book.epub",
      "acquisition_id": HostileStatus(ACQ)}, "failed", "Import ACSM non confirmé"),
    ({"status": "complete", "path": "/tmp/book.epub",
      "acquisition_id": "manual-acsm:invalid"}, "failed", "Import ACSM non confirmé"),
    ({"status": "complete", "path": "/tmp/\\x00.epub",
      "acquisition_id": ACQ}, "failed", "Import ACSM non confirmé"),
    ({"status": HostileStatus("complete")}, "failed", "Import ACSM non confirmé"),
    (HostileResult(status="complete"), "failed", "Import ACSM non confirmé"),
    ({"status": "retryable_error", "url": "https://PRIVATE?token=SECRET"},
     "failed", "Import ACSM non confirmé"),
    (None, "failed", "Import ACSM non confirmé"),
])
def test_acsm_task_feedback_requires_core_attestation(caplog, receipt, expected, message):
    runner, displayed, flush, logger = _runner()
    completed, failed = [], []
    try:
        with caplog.at_level(logging.INFO, logger=logger.name):
            runner.submit(
                "Import ACSM…", lambda: receipt, ok=completed.append,
                err=failed.append, kind="acsm-import", origin="user",
            )
            flush()
        terminal = [args for name, args in displayed if name == "finished"]
        assert len(terminal) == 1
        assert terminal[0][3] is (expected == "success")
        assert terminal[0][4] == message
        if expected == "pending":
            assert terminal[0][5] == "pending"
        assert completed == [receipt]
        assert failed == []
        traces = [row.getMessage() for row in caplog.records
                  if "TRACE task outcome id=" in row.getMessage()]
        assert len(traces) == 1
        assert "kind=acsm-import origin=user" in traces[0]
        assert f"business_status={expected}" in traces[0]
        assert "status=" + ("success" if expected == "success" else "failed") in traces[0]
        assert "PRIVATE" not in caplog.text and "SECRET" not in caplog.text
    finally:
        runner.close()


def test_unrelated_library_task_keeps_legacy_success_classification():
    runner, displayed, flush, _logger = _runner()
    try:
        runner.submit("Library", lambda: {"status": "attention_required"},
                      kind="library", origin="user")
        flush()
        assert [args[3] for name, args in displayed if name == "finished"] == [True]
    finally:
        runner.close()
