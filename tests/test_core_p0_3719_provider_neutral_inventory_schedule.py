"""#3719: a private inventory schedule is capability-based, not provider-ID based.

Synthetic providers only; SQLite v14, explicit opt-in and no provider network.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from cifereca.brokers import SynchronizationBrokerImpl
from cifereca.core.source_refresh_policy import inventory_source_eligible
from cifereca.models import Account
from cifereca.storage import Storage


def _source(tmp_path, *, provider="unknown-private-store", personal_library=True):
    storage = Storage(tmp_path / "schedule.sqlite3")
    account = Account(
        display_name="Fixture", unique_id="fixture-account",
        provider_id=provider, credential_id="fixture-credential",
        provider_label="Fixture", identity_id="fixture-identity",
    )
    session = NS(
        provider_id=provider, credential_id=account.credential_id,
        connected=True,
        capabilities=NS(personal_library=personal_library, loans=not personal_library),
    )
    calls = []
    service = NS(
        storage=storage, accounts=[account],
        _sessions={f"{provider}::{account.credential_id}": session},
        _session_key=lambda p, c: f"{p}::{c}",
        sync_source_enabled=lambda _account: True,
        identity_by_id=lambda identity: (
            NS(identity_id=identity) if identity == "fixture-identity" else None
        ),
        synchronize_account_source=lambda a, **options: (
            calls.append((a.identity_id, a.provider_id, options)) or {"library": 1}
        ),
    )
    service._synchronize_scheduled_account_inventory = (
        lambda a, **options: service.synchronize_account_source(
            a, inventory_only=True, **options,
        )
    )
    service.accounts_for_identity = lambda identity=None: [
        a for a in service.accounts if identity is None or a.identity_id == identity
    ]
    connection = storage.provider_connection_put({
        "identity_id": account.identity_id,
        "provider_id": provider,
        "credential_id": account.credential_id,
        "enabled": True,
    })
    storage.provider_account_put({
        "connection_id": connection, "identity_id": account.identity_id,
        "provider_id": provider, "credential_id": account.credential_id,
        "account_id": account.unique_id,
    })
    return service, account, session, calls


@pytest.mark.parametrize("provider", ["unknown-private-store", "reference-private"])
def test_unknown_private_provider_can_explicitly_schedule_inventory(tmp_path, provider):
    service, account, session, calls = _source(tmp_path, provider=provider)
    try:
        broker = SynchronizationBrokerImpl(service)
        sid = broker._source_id(account)
        source = broker.sources("fixture-identity")[0]
        assert "remote_inventory" in source.sync_capabilities
        assert "scheduled_refresh" in source.sync_capabilities
        assert inventory_source_eligible(service, account)
        assert broker.inventory_refresh_schedule(sid)["mode"] == "manual"
        assert broker.synchronize_scheduled_inventory(
            "fixture-identity", "startup"
        ).facts["attempted"] == 0
        assert calls == []

        broker.set_inventory_refresh_schedule(sid, "on_start", interval_minutes=15)
        result = broker.synchronize_scheduled_inventory("fixture-identity", "startup")
        assert result.ok and result.facts["attempted"] == 1
        assert result.facts["acquisition_authorized"] is False
        assert calls == [("fixture-identity", provider, {"deep": False, "control": None, "inventory_only": True})]
        state = broker._state(sid)
        assert state["inventory_status"] == "success"
        assert state["inventory_last_attempt"] and state["inventory_last_success"]
        phase = broker.sources("fixture-identity")[0].phase_states[0]
        assert phase.phase == "discover_rights" and phase.status == "success"
        assert phase.last_success == state["inventory_last_success"]
        assert broker.synchronize_scheduled_inventory(
            "fixture-identity", "startup"
        ).facts["attempted"] == 0
        assert len(calls) == 1
    finally:
        service.storage.close()


def test_unknown_provider_schedule_rechecks_live_owner_and_connection(tmp_path):
    service, account, session, calls = _source(tmp_path)
    try:
        broker = SynchronizationBrokerImpl(service)
        sid = broker._source_id(account)
        broker.set_inventory_refresh_schedule(sid, "on_start")
        session.connected = False
        assert broker.inventory_refresh_schedule(sid)["mode"] == "on_start"
        assert broker.synchronize_scheduled_inventory(
            "fixture-identity", "startup"
        ).facts["attempted"] == 0
        with pytest.raises(ValueError, match="not eligible"):
            broker.set_inventory_refresh_schedule(sid, "on_start")
        session.connected = True
        service.storage.provider_connection_put({
            "identity_id": "foreign-identity",
            "provider_id": account.provider_id,
            "credential_id": account.credential_id,
            "enabled": True,
        })
        assert not inventory_source_eligible(service, account)
        assert broker.synchronize_scheduled_inventory(
            "fixture-identity", "startup"
        ).facts["attempted"] == 0
        assert calls == []
    finally:
        service.storage.close()


def test_loan_only_provider_never_inherits_private_inventory_schedule(tmp_path):
    service, account, _session, calls = _source(
        tmp_path, provider="fixture-loans", personal_library=False,
    )
    try:
        broker = SynchronizationBrokerImpl(service)
        sid = broker._source_id(account)
        source = broker.sources("fixture-identity")[0]
        assert "entitlements" in source.sync_capabilities
        assert "remote_inventory" not in source.sync_capabilities
        assert "scheduled_refresh" not in source.sync_capabilities
        assert not inventory_source_eligible(service, account)
        with pytest.raises(ValueError, match="not supported"):
            broker.inventory_refresh_schedule(sid)
        with pytest.raises(ValueError, match="not supported"):
            broker.set_inventory_refresh_schedule(sid, "on_start")
        assert broker.synchronize_scheduled_inventory(
            "fixture-identity", "startup"
        ).facts["attempted"] == 0
        assert calls == []
    finally:
        service.storage.close()


def test_scheduled_inventory_never_falls_back_to_public_sync_when_internal_missing(tmp_path):
    """A plugin service without the Core-only ABI cannot invoke A→B by schedule."""
    service, account, _session, calls = _source(tmp_path)
    try:
        del service._synchronize_scheduled_account_inventory
        broker = SynchronizationBrokerImpl(service)
        sid = broker._source_id(account)
        broker.set_inventory_refresh_schedule(sid, "on_start")
        result = broker.synchronize_scheduled_inventory("fixture-identity", "startup")
        assert not result.ok
        assert result.facts == {
            "phase": "discover_rights", "trigger": "startup",
            "attempted": 1, "failed": 1, "acquisition_authorized": False,
        }
        assert calls == []
        assert broker._state(sid).get("inventory_status") == "failed"
        assert not broker._state(sid).get("inventory_last_success")
    finally:
        service.storage.close()
