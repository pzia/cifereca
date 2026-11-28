"""#8437: offline characterization of observed sync verdict defects.

Temporary baseline evidence; assertions describe current behavior, not target.
"""
from types import SimpleNamespace as NS

import pytest

from cifereca.brokers import SynchronizationBrokerImpl
from cifereca.core.sync_coordination import opening_sync
from cifereca.locking import InterProcessLock, LockBusyError
from cifereca.providers import ProviderConfigurationError
from tests.test_core_p0_3719_provider_neutral_inventory_schedule import _source
from tests.test_core_p0_1346_kobo_on_start_inventory_gui import _startup_window


def test_opening_worker_loses_typed_lock_cause(tmp_path):
    path = tmp_path / "writer.lock"
    core = NS(_opening_sync_locked=lambda **kw: pytest.fail("entered locked work"))
    r, _, run_opening = _startup_window()
    r.window.service.opening_sync = lambda **kw: opening_sync(
        core, lock_factory=InterProcessLock, lock_path=lambda: path, **kw
    )
    with InterProcessLock(path):
        with pytest.raises(ProviderConfigurationError) as original:
            r.window.service.opening_sync()
        assert isinstance(original.value.__cause__, LockBusyError)
        run_opening(r.window)
        with pytest.raises(RuntimeError) as rendered:
            r.jobs.pop().work(lambda *_: None)
        assert type(rendered.value) is RuntimeError
        assert rendered.value.__cause__ is None


def test_scheduled_busy_is_failed_and_blocks_retry_after_release(tmp_path):
    service, account, _, calls = _source(tmp_path)
    path = tmp_path / "writer.lock"
    original = service._synchronize_scheduled_account_inventory

    def scheduled(a, **kw):
        try:
            with InterProcessLock(path):
                return original(a, **kw)
        except LockBusyError as exc:
            raise ProviderConfigurationError("fixture busy") from exc

    service._synchronize_scheduled_account_inventory = scheduled
    try:
        broker = SynchronizationBrokerImpl(service)
        sid = broker._source_id(account)
        broker.set_inventory_refresh_schedule(sid, "on_start")
        with InterProcessLock(path):
            result = broker.synchronize_scheduled_inventory("fixture-identity", "startup")
        assert not result.ok and result.facts["failed"] == 1
        state = broker._state(sid)
        assert state["inventory_status"] == "busy"
        assert state["inventory_last_attempt"]
        assert not state.get("inventory_last_success")
        # Fresh broker removes in-memory startup singleflight from the equation.
        retry = SynchronizationBrokerImpl(service).synchronize_scheduled_inventory(
            "fixture-identity", "startup"
        )
        assert retry.facts["attempted"] == 0
        assert calls == []
    finally:
        service.storage.close()


def test_scheduled_partial_loses_status_despite_positive_count(tmp_path):
    service, account, _, _ = _source(tmp_path)
    service._synchronize_scheduled_account_inventory = lambda *a, **kw: {
        "status": "partial", "library": 5,
    }
    try:
        broker = SynchronizationBrokerImpl(service)
        sid = broker._source_id(account)
        broker.set_inventory_refresh_schedule(sid, "on_start")
        result = broker.synchronize_scheduled_inventory("fixture-identity", "startup")
        assert not result.ok
        assert result.changed == 5
        assert result.facts["failed"] == 1
        assert "status" not in result.facts
        assert broker._state(sid)["inventory_status"] == "partial"
        assert not broker._state(sid).get("inventory_last_success")
    finally:
        service.storage.close()
