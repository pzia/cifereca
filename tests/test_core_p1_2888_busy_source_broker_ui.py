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
        before = broker._state(source_id)
        with caplog.at_level(logging.INFO, logger="cifereca"):
            result = broker.synchronize_source(source_id)
        assert result.ok is False
        assert result.facts == {"status": "busy", "reason": "operation_busy"}
        assert "PRIVATE_" not in repr(result)
        row = broker.sources()[0]
        # A denied admission is observable in the task receipt, not a
        # persistent running/busy state that could suppress future refresh.
        assert broker._state(source_id) == before
        assert row.status == "idle"
        assert row.error == ""
        assert row.contention_observed is True
        assert sync_source_attention(row) == ("warning", "Notices et droits : déjà en cours")
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


def test_contention_projection_expires_and_invalidates_on_new_evidence(tmp_path, monkeypatch):
    import time
    clock = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    storage = Storage(tmp_path / "projection.sqlite3")
    try:
        broker = fake_broker(storage)
        sid = broker.sources()[0].source_id
        key = "sync.source.state." + sid
        before = storage.metadata_get(key)
        broker._mark_contention(sid, before)
        assert broker.sources()[0].contention_observed
        assert storage.metadata_get(key) == before
        clock[0] += 30
        assert not broker.sources()[0].contention_observed
        broker._mark_contention(sid, before)
        broker._set_state(sid, {"status": "ok"})
        assert not broker.sources()[0].contention_observed
        assert broker.sources()[0].status == "ok"
    finally:
        storage.close()
