"""#2888: Core's observed pending state cannot become a failed download."""
import logging
from types import MappingProxyType

import pytest

from cifereca.plugin_api import SyncResult
from tests.test_core_p0_981_redacted_task_traces import _runner


@pytest.mark.parametrize(("facts", "expected", "message"), [
    ({"status": "partial", "canonical_execution": {
        "pending": 8, "blocked": 0, "failed": 0,
    }}, "pending", "Synchronisation en attente de livraison"),
    ({"status": "partial", "canonical_execution": MappingProxyType({
        "pending": 8, "blocked": 0, "failed": 0,
    })}, "pending", "Synchronisation en attente de livraison"),
    ({"status": "partial", "canonical_execution": {
        "pending": 0, "blocked": 2, "failed": 0,
    }}, "blocked", "Synchronisation bloquée"),
    ({"status": "partial", "canonical_execution": {
        "pending": 8, "blocked": 0, "failed": 1,
    }}, "partial", "Synchronisation partielle"),
    ({"status": "cancelled"}, "cancelled", "Synchronisation annulée"),
    ({"reason": "operation_busy"}, "busy", "Synchronisation déjà en cours"),
    ({"status": "failed"}, "failed", "Échec de synchronisation"),
    ({"sources": {"one": {"status": "pending"}}},
     "pending", "Synchronisation en attente de livraison"),
    ({"sources": {"one": {"status": "blocked"}}},
     "blocked", "Synchronisation bloquée"),
    ({"status": "partial", "canonical_execution": {
        "pending": True, "blocked": 0, "failed": 0,
    }}, "partial", "Synchronisation partielle"),
])
def test_exact_business_outcome_is_not_a_fake_download_failure(
    caplog, facts, expected, message,
):
    runner, displayed, flush, logger = _runner()
    receipt = SyncResult(ok=False, facts=facts)
    callbacks = []
    try:
        with caplog.at_level(logging.INFO, logger=logger.name):
            runner.submit("Source", lambda: receipt, ok=callbacks.append,
                          kind="source-sync", origin="automatic",
                          success_toast="Downloaded")
            flush()
        assert callbacks == [receipt]
        finish = [args for name, args in displayed if name == "finished"]
        assert len(finish) == 1
        assert finish[0][3] is False
        assert finish[0][4] == message
        assert f"business_status={expected}" in caplog.text
        assert "status=success" not in caplog.text
    finally:
        runner.close()


def test_positive_callback_cannot_erase_a_closed_partial_core_status():
    runner, _displayed, _flush, _logger = _runner()
    try:
        assert runner._source_sync_outcome(
            SyncResult(ok=True, facts={"status": "partial"}), "source-sync",
        ) == "partial"
        assert runner._source_sync_outcome(
            SyncResult(ok=True), "source-sync",
        ) == "success"
        assert runner._source_sync_outcome(
            SyncResult(ok=False, facts={"status": "pending"}), "library",
        ) == "success"
    finally:
        runner.close()
