"""#2888: a Core lock collision is busy, not a failed HTTP acquisition."""
import logging

from cifereca.gui.sync_view import (
    sync_source_attention, sync_source_presentation, sync_source_phase_summary,
)
from cifereca.locking import LockBusyError
from cifereca.providers import ProviderConfigurationError
from cifereca.storage import Storage
from tests.test_core_0978_source_state import fake_broker
from tests.test_core_p0_981_redacted_task_traces import _runner


def _busy(_account, **_kw):
    try:
        raise LockBusyError("PRIVATE_LOCK_PATH")
    except LockBusyError as cause:
        raise ProviderConfigurationError("PRIVATE_PROVIDER_ERROR") from cause


def test_core_lock_busy_reaches_typed_task_and_gtk_as_busy(tmp_path, caplog):
    storage = Storage(tmp_path / "busy.sqlite3")
    try:
        broker = fake_broker(storage)
        source_id = broker.sources()[0].source_id
        broker.service.synchronize_account_source = _busy
        with caplog.at_level(logging.INFO, logger="cifereca"):
            result = broker.synchronize_source(source_id)
        assert result.ok is False
        assert result.facts == {"status": "busy", "reason": "operation_busy"}
        assert "PRIVATE_" not in repr(result)
        row = broker.sources()[0]
        assert row.status == "busy"
        assert row.error == ""
        assert row.phase_states[0].status == "busy"
        assert sync_source_attention(row) == (
            "warning", "Notices et droits : déjà en cours",
        )
        assert sync_source_presentation(row).sync_label == "Déjà en cours"
        assert "Déjà en cours" in sync_source_phase_summary(row)
        runner, displayed, flush, logger = _runner()
        try:
            with caplog.at_level(logging.INFO, logger=logger.name):
                runner.submit("Private sync", lambda: result, kind="source-sync")
                flush()
            end = [args for kind, args in displayed if kind == "finished"]
            assert end[-1][3:5] == (False, "Synchronisation déjà en cours")
            assert "business_status=busy" in caplog.text
        finally:
            runner.close()
    finally:
        storage.close()


def test_unrelated_exception_or_unproven_busy_cause_remains_failure(tmp_path):
    storage = Storage(tmp_path / "unrelated.sqlite3")
    try:
        broker = fake_broker(storage)
        source_id = broker.sources()[0].source_id

        def unrelated(_account, **_kw):
            try:
                raise LockBusyError("PRIVATE_LOCK")
            except LockBusyError as cause:
                raise RuntimeError("PRIVATE_FAIL") from cause

        for bad in (
            unrelated,
            lambda _account, **_kw: (_ for _ in ()).throw(
                ProviderConfigurationError("Operation not proven busy")
            ),
        ):
            broker.service.synchronize_account_source = bad
            result = broker.synchronize_source(source_id)
            assert result.facts == {"reason": "source_unavailable"}
            assert broker.sources()[0].status == "error"
            assert "PRIVATE_" not in repr(result)
    finally:
        storage.close()
