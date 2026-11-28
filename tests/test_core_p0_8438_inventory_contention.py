"""#8438/#8439: denied admission and partial scheduled inventory."""
from cifereca.brokers import SynchronizationBrokerImpl
from cifereca.locking import InterProcessLock, LockBusyError
from cifereca.providers import ProviderConfigurationError
from tests.test_core_p0_3719_provider_neutral_inventory_schedule import _source


def test_busy_does_not_consume_startup_or_inventory_clock(tmp_path):
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
        before = broker._state(sid)
        with InterProcessLock(path):
            result = broker.synchronize_scheduled_inventory("fixture-identity", "startup")
        assert not result.ok
        assert result.facts["status"] == "busy"
        assert result.facts["attempted"] == result.facts["failed"] == 0
        assert result.facts["deferred"] == 1
        assert broker._state(sid) == before
        assert calls == []
        retry = broker.synchronize_scheduled_inventory("fixture-identity", "startup")
        assert retry.ok and retry.facts["attempted"] == 1
        assert len(calls) == 1
        assert calls[0][2]["inventory_only"] is True
        assert broker.synchronize_scheduled_inventory(
            "fixture-identity", "startup"
        ).facts["attempted"] == 0
        assert len(calls) == 1
    finally:
        service.storage.close()


def test_partial_inventory_keeps_positive_count_without_success(tmp_path):
    service, account, _, _ = _source(tmp_path)
    service._synchronize_scheduled_account_inventory = lambda *a, **kw: {
        "status": "partial", "library": 5,
    }
    try:
        broker = SynchronizationBrokerImpl(service)
        sid = broker._source_id(account)
        broker.set_inventory_refresh_schedule(sid, "on_start")
        result = broker.synchronize_scheduled_inventory("fixture-identity", "startup")
        assert not result.ok and result.changed == 5
        assert result.facts["status"] == "partial"
        assert result.facts["partial"] == 1
        assert result.facts["failed"] == 0
        assert not broker._state(sid).get("inventory_last_success")
    finally:
        service.storage.close()


def test_denied_admission_must_not_overwrite_newer_terminal_evidence(tmp_path):
    service, account, _, _ = _source(tmp_path)
    broker = SynchronizationBrokerImpl(service)
    sid = broker._source_id(account)
    broker.set_inventory_refresh_schedule(sid, "on_start")
    newer = {
        "status": "ok", "inventory_status": "success",
        "inventory_last_attempt": "2026-11-28T07:00:00+00:00",
        "inventory_last_success": "2026-11-28T07:00:00+00:00",
    }

    def denied(a, **kw):
        # Deterministic interleaving: the other writer publishes after
        # our provisional state and before our denial is handled.
        broker._set_state(sid, newer)
        try:
            raise LockBusyError("fixture")
        except LockBusyError as exc:
            raise ProviderConfigurationError("fixture") from exc

    service._synchronize_scheduled_account_inventory = denied
    try:
        result = broker.synchronize_scheduled_inventory("fixture-identity", "startup")
        assert result.facts["reason"] == "operation_busy"
        assert broker._state(sid) == newer
    finally:
        service.storage.close()


def test_metadata_publication_rejects_stale_snapshot(tmp_path):
    service, _, _, _ = _source(tmp_path)
    try:
        storage = service.storage
        key = "fixture.cas"
        assert storage.metadata_compare_publish(key, None, "first")
        assert not storage.metadata_compare_publish(key, None, "stale")
        assert storage.metadata_compare_publish(key, "first", "newer")
        storage.metadata_compare_restore(key, "first", None)
        assert storage.metadata_get(key) == "newer"
        storage.metadata_compare_restore(key, "newer", None)
        assert storage.metadata_get(key) is None
    finally:
        service.storage.close()
