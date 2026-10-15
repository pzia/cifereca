"""#1346: direct source sync cannot bypass the GTK connection/capability gate."""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from cifereca.brokers import SynchronizationBrokerImpl
from cifereca.plugin_api import SyncRequest
from tests.test_core039_contract_surface import FakeService


def _setup():
    service = FakeService()
    broker = SynchronizationBrokerImpl(service)
    rows = {row.account_id: row for row in broker.sources("alice")}
    return service, broker, rows


@pytest.mark.parametrize("change", ["disconnected", "missing_session"])
def test_stale_direct_source_sync_does_not_reconnect_or_attempt_work(change):
    service, broker, rows = _setup()
    target = rows["a1"]
    key = service._session_key("paris", "card-1")
    if change == "disconnected":
        service._sessions[key].connected = False
    else:
        del service._sessions[key]
    before = dict(service.storage.values)
    result = broker.synchronize_source(target.source_id)
    assert not result.ok and result.changed == 0
    assert result.facts == {"reason": "source_disconnected"}
    assert result.warnings == ("source disconnected",)
    assert service.calls == []
    assert service.storage.values == before
    assert broker.sources("alice")[0].connected is False


def test_connected_catalogue_without_loans_or_library_cannot_be_synced():
    service, broker, rows = _setup()
    service._sessions[service._session_key("paris", "card-1")].capabilities = NS(
        catalog=True, loans=False, personal_library=False,
    )
    before = dict(service.storage.values)
    result = broker.synchronize_source(
        rows["a1"].source_id, SyncRequest(identity_id="alice"),
    )
    assert not result.ok and result.changed == 0
    assert result.facts == {"reason": "not_supported"}
    assert result.warnings == ("source sync unsupported",)
    assert service.calls == []
    assert service.storage.values == before


def test_unrelated_connected_account_is_still_synchronizable():
    service, broker, rows = _setup()
    service._sessions[service._session_key("paris", "card-1")].connected = False
    first = broker.synchronize_source(rows["a1"].source_id)
    second = broker.synchronize_source(rows["a2"].source_id)
    assert first.facts == {"reason": "source_disconnected"}
    assert second.ok and second.changed == 2
    assert service.calls == [("a2", False)]


def test_disabled_source_stays_skipped_without_forcing_reconnect():
    service, broker, rows = _setup()
    broker.set_enabled(rows["a1"].source_id, False)
    service._sessions[service._session_key("paris", "card-1")].connected = False
    before = dict(service.storage.values)
    result = broker.synchronize_source(rows["a1"].source_id)
    assert result.ok and result.changed == 0 and result.facts["skipped"] is True
    assert service.calls == []
    assert service.storage.values == before


def test_scope_rejection_precedes_connection_gate():
    service, broker, rows = _setup()
    service._sessions[service._session_key("paris", "card-1")].connected = False
    result = broker.synchronize_source(
        rows["a1"].source_id,
        SyncRequest(identity_id="other-private-reader"),
    )
    assert result.facts == {"reason": "identity_mismatch"}
    assert not result.ok and service.calls == []


def test_batch_and_direct_calls_do_not_bypass_live_state():
    service, broker, rows = _setup()
    # A source can disconnect after the list of sources has been projected.
    assert rows["a1"].connected and rows["a2"].connected
    service._sessions[service._session_key("paris", "card-2")].connected = False
    outcome = broker.synchronize(SyncRequest(identity_id="alice"))
    assert outcome.ok and outcome.changed == 2
    assert service.calls == [("a1", False)]
    # A stale explicit source route still rejects the disconnected account.
    rejected = broker.synchronize_source(rows["a2"].source_id)
    assert rejected.facts == {"reason": "source_disconnected"}
    assert service.calls == [("a1", False)]
