"""#2888: only attested Core A→B counts reach the typed GTK sync receipt.

No provider I/O, no new acquisition and no inferred deliveries.
"""
from __future__ import annotations

from cifereca.core.diagnostic_source_sync import CANONICAL_EXECUTION_STATUSES
from cifereca.storage import Storage
from tests.test_core_0978_source_state import fake_broker
from tests.test_core_p0_981_redacted_task_traces import _runner


def _execution(**overrides):
    values = {state: 0 for state in CANONICAL_EXECUTION_STATUSES}
    values.update(overrides)
    return values


def test_kobo_like_pending_broker_receipt_does_not_become_failed_download(tmp_path):
    storage = Storage(tmp_path / "source.sqlite3")
    try:
        broker = fake_broker(storage)
        source = broker.sources()[0]
        broker.service.synchronize_account_source = lambda account, **_: {
            "status": "partial", "loans": 9,
            "canonical_execution": _execution(
                present_attested=1, pending=8,
            ),
            "canonical_rights": {"observed": 9},
            "url": "PRIVATE_SIGNED_URL", "token": "PRIVATE_COOKIE",
        }
        receipt = broker.synchronize_source(source.source_id)
        assert receipt.ok is False
        assert receipt.facts["status"] == "partial"
        assert receipt.facts["canonical_execution"] == _execution(
            present_attested=1, pending=8,
        )
        assert receipt.facts["canonical_execution"]["failed"] == 0
        runner, _displayed, _flush, _logger = _runner()
        try:
            assert runner._source_sync_outcome(receipt, "source-sync") == "pending"
        finally:
            runner.close()
        assert "PRIVATE_" not in repr(receipt)
        assert broker.sources()[0].status == "partial"
    finally:
        storage.close()


def test_real_failed_delivery_remains_partial_and_is_not_rewritten_as_pending(tmp_path):
    storage = Storage(tmp_path / "failed.sqlite3")
    try:
        broker = fake_broker(storage)
        broker.service.synchronize_account_source = lambda account, **_: {
            "status": "partial", "canonical_execution": _execution(
                present_attested=1, failed=1, pending=8,
            ),
        }
        receipt = broker.synchronize_source(broker.sources()[0].source_id)
        assert receipt.facts["canonical_execution"]["failed"] == 1
        runner, _displayed, _flush, _logger = _runner()
        try:
            assert runner._source_sync_outcome(receipt, "source-sync") == "partial"
        finally:
            runner.close()
    finally:
        storage.close()


def test_missing_malformed_and_untrusted_envelopes_are_not_invented(tmp_path):
    storage = Storage(tmp_path / "invalid.sqlite3")
    try:
        broker = fake_broker(storage)
        src = broker.sources()[0].source_id
        for counts in (
            {"pending": 8},
            {**_execution(), "failed": True},
            {**_execution(), "pending": -1},
            {**_execution(), "pending": 1_000_001},
            {**_execution(), "secret": "PRIVATE_SIGNED_URL"},
            "PRIVATE_SIGNED_URL",
            None,
        ):
            broker.service.synchronize_account_source = (
                lambda account, execution=counts, **_: {
                    "status": "partial", "canonical_execution": execution,
                }
            )
            receipt = broker.synchronize_source(src)
            assert "canonical_execution" not in receipt.facts
            assert "PRIVATE_" not in repr(receipt)
        broker.service.synchronize_account_source = (
            lambda account, **_: {"status": "partial"}
        )
        assert "canonical_execution" not in broker.synchronize_source(src).facts
    finally:
        storage.close()
