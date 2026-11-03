"""P0 #3719: scheduling is a restrictive private dispatch, not public Sync ABI."""
from inspect import signature

from cifereca.brokers import SynchronizationBrokerImpl
from tests.test_core_p0_3719_provider_neutral_inventory_schedule import _source


def test_plugin_broker_synchronize_source_signature_remains_public_and_stable():
    parameters = signature(SynchronizationBrokerImpl.synchronize_source).parameters
    assert tuple(parameters) == ("self", "source_id", "request", "control")
    assert parameters["request"].default is None
    assert parameters["control"].default is None
    # An external caller cannot substitute an internal scheduled invocation.
    assert "inventory_only" not in parameters
    assert "_inventory_only" not in parameters


def test_scheduled_refresh_never_dispatches_through_public_source_sync(tmp_path, monkeypatch):
    service, account, _session, calls = _source(tmp_path)
    try:
        def forbidden_public(*_args, **_kwargs):
            raise AssertionError("scheduled inventory called public source Sync")
        monkeypatch.setattr(
            SynchronizationBrokerImpl, "synchronize_source", forbidden_public,
        )
        broker = SynchronizationBrokerImpl(service)
        source_id = broker._source_id(account)
        broker.set_inventory_refresh_schedule(source_id, "on_start")
        result = broker.synchronize_scheduled_inventory("fixture-identity", "startup")
        assert result.ok
        assert result.facts["attempted"] == 1
        assert result.facts["acquisition_authorized"] is False
        assert calls == [
            ("fixture-identity", account.provider_id, {
                "deep": False, "control": None, "inventory_only": True,
            })
        ]
        assert broker._state(source_id)["scope"] == "scheduled"
    finally:
        service.storage.close()
