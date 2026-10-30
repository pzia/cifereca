"""P0 #1346: per-Kobo-account inventory refresh, never local acquisition."""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace as NS

import pytest

from cifereca.brokers import SynchronizationBrokerImpl
from cifereca.core.source_materialization_schedule import policy_key as acquisition_policy_key
from cifereca.core.source_refresh_policy import kobo_inventory_source_eligible
from cifereca.models import Account
from cifereca.plugin_api import SourceInventoryRefreshScheduleBroker
from cifereca.storage import Storage


def _rig(tmp_path):
    storage = Storage(tmp_path / "inventory-refresh.sqlite3")
    account = Account(
        display_name="Kobo Alice", unique_id="alice-primary",
        provider_id="kobo", credential_id="vault-alice",
        provider_label="Kobo", identity_id="alice",
    )
    session = NS(
        provider_id="kobo", credential_id="vault-alice", connected=True,
        capabilities=NS(personal_library=True, backup=False),
    )
    calls = []
    source = NS(
        storage=storage, accounts=[account],
        _sessions={"kobo::vault-alice": session},
        _session_key=lambda provider, credential: f"{provider}::{credential}",
        accounts_for_identity=lambda identity=None: [
            a for a in source.accounts if identity is None or a.identity_id == identity
        ],
        sync_source_enabled=lambda _account: True,
        identity_by_id=lambda identity: NS(identity_id=identity) if identity == "alice" else None,
        synchronize_account_source=lambda a, **options: (
            calls.append((a.identity_id, a.credential_id, options))
            or {"library": 1}
        ),
    )
    storage.provider_connection_put({
        "identity_id": "alice", "provider_id": "kobo",
        "credential_id": "vault-alice", "enabled": True,
    })
    return source, account, session, calls


def test_manual_default_does_not_dispatch_and_never_inherits_backup_consent(tmp_path):
    service, account, session, calls = _rig(tmp_path)
    try:
        broker = SynchronizationBrokerImpl(service)
        assert isinstance(broker, SourceInventoryRefreshScheduleBroker)
        sid = broker._source_id(account)
        service.storage.metadata_set("kobo_backup.enabled", "true")
        assert broker.inventory_refresh_schedule(sid) == {
            "mode": "manual", "interval_minutes": 60,
        }
        assert broker.synchronize_scheduled_inventory("alice", "startup").facts["attempted"] == 0
        assert calls == []
        assert service.storage.metadata_get(acquisition_policy_key(sid)) is None
        # No backup capability is necessary for *inventory* synchronization.
        assert session.capabilities.backup is False
        assert kobo_inventory_source_eligible(service, account)
    finally:
        service.storage.close()


def test_startup_opt_in_dispatches_once_for_exact_source_and_reopen_throttles(tmp_path):
    service, account, _session, calls = _rig(tmp_path)
    try:
        broker = SynchronizationBrokerImpl(service)
        sid = broker._source_id(account)
        assert broker.set_inventory_refresh_schedule(sid, "on_start", interval_minutes=15) == {
            "mode": "on_start", "interval_minutes": 15,
        }
        result = broker.synchronize_scheduled_inventory("alice", "startup")
        assert result.ok
        assert result.facts["attempted"] == 1
        assert result.facts["acquisition_authorized"] is False
        assert len(calls) == 1 and calls[0][:2] == ("alice", "vault-alice")
        assert calls[0][2]["deep"] is False
        assert broker.synchronize_scheduled_inventory("alice", "startup").facts["attempted"] == 0
        assert SynchronizationBrokerImpl(service).synchronize_scheduled_inventory(
            "alice", "startup",
        ).facts["attempted"] == 0
        unknown = broker.synchronize_scheduled_inventory("bob", "startup")
        assert not unknown.ok and unknown.facts == {"reason": "unknown_identity"}
        assert len(calls) == 1
        assert service.storage.metadata_get(acquisition_policy_key(sid)) is None
    finally:
        service.storage.close()


def test_periodic_policy_only_dispatches_on_due_periodic_tick(tmp_path):
    service, account, _, calls = _rig(tmp_path)
    try:
        broker = SynchronizationBrokerImpl(service)
        sid = broker._source_id(account)
        broker.set_inventory_refresh_schedule(sid, "periodic", interval_minutes=30)
        assert broker.synchronize_scheduled_inventory("alice", "startup").facts["attempted"] == 0
        assert broker.synchronize_scheduled_inventory("alice", "periodic").facts["attempted"] == 1
        assert broker.synchronize_scheduled_inventory("alice", "periodic").facts["attempted"] == 0
        assert len(calls) == 1
        state = broker._state(sid)
        assert state["last_attempt"] and state["last_success"]
        assert state["scope"] == "manual"  # existing stable sync reporting, not acquisition
    finally:
        service.storage.close()


@pytest.mark.parametrize("invalid", ["periodically", "materialize", 1, True, None])
def test_bad_schedule_modes_never_write_or_execute(tmp_path, invalid):
    service, account, _session, calls = _rig(tmp_path)
    try:
        broker = SynchronizationBrokerImpl(service)
        sid = broker._source_id(account)
        with pytest.raises(ValueError):
            broker.set_inventory_refresh_schedule(sid, invalid)
        assert broker.inventory_refresh_schedule(sid)["mode"] == "manual"
        assert broker.synchronize_scheduled_inventory("alice", "startup").facts["attempted"] == 0
        assert calls == []
    finally:
        service.storage.close()


@pytest.mark.parametrize("invalid", ["", "private-token", None, 0])
def test_scheduler_requires_an_explicit_identity_and_supported_trigger(tmp_path, invalid):
    service, account, _session, calls = _rig(tmp_path)
    try:
        broker = SynchronizationBrokerImpl(service)
        assert not broker.synchronize_scheduled_inventory(invalid, "startup").ok
        assert not broker.synchronize_scheduled_inventory("alice", invalid).ok
        assert calls == []
    finally:
        service.storage.close()


def test_revocation_and_rebinding_disable_existing_schedule_without_network(tmp_path):
    service, account, session, calls = _rig(tmp_path)
    try:
        broker = SynchronizationBrokerImpl(service)
        sid = broker._source_id(account)
        broker.set_inventory_refresh_schedule(sid, "on_start")
        session.connected = False
        assert broker.synchronize_scheduled_inventory("alice", "startup").facts["attempted"] == 0
        session.connected = True
        broker.set_enabled(sid, False)
        assert broker.synchronize_scheduled_inventory("alice", "startup").facts["attempted"] == 0
        with pytest.raises(ValueError):
            broker.set_inventory_refresh_schedule(sid, "on_start")
        broker.set_enabled(sid, True)
        service.storage.provider_connection_put({
            "identity_id": "bob", "provider_id": "kobo",
            "credential_id": "vault-alice", "enabled": True,
        })
        assert broker.synchronize_scheduled_inventory("alice", "startup").facts["attempted"] == 0
        assert calls == []
        assert broker.set_inventory_refresh_schedule(sid, "manual")["mode"] == "manual"
    finally:
        service.storage.close()


def test_duplicate_credential_cannot_opt_in_or_auto_refresh(tmp_path):
    service, account, _, calls = _rig(tmp_path)
    try:
        broker = SynchronizationBrokerImpl(service)
        sid = broker._source_id(account)
        broker.set_inventory_refresh_schedule(sid, "on_start")
        service.accounts.append(Account(
            display_name="Kobo Bob", unique_id="bob-primary",
            provider_id="kobo", credential_id="vault-alice",
            provider_label="Kobo", identity_id="bob",
        ))
        assert broker.synchronize_scheduled_inventory("alice", "startup").facts["attempted"] == 0
        with pytest.raises(ValueError):
            broker.set_inventory_refresh_schedule(sid, "on_start")
        assert calls == []
    finally:
        service.storage.close()


def test_running_or_corrupted_phase_never_reschedules_without_user_action(tmp_path):
    service, account, _, calls = _rig(tmp_path)
    try:
        broker = SynchronizationBrokerImpl(service)
        sid = broker._source_id(account)
        broker.set_inventory_refresh_schedule(sid, "periodic", interval_minutes=15)
        raw_key = "sync.source.state." + sid
        before = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        service.storage.metadata_set(raw_key, json.dumps({
            "last_attempt": before, "status": "running",
        }))
        assert broker.synchronize_scheduled_inventory("alice", "periodic").facts["attempted"] == 0
        service.storage.metadata_set(raw_key, "{PRIVATE-CORRUPT-TOKEN")
        assert broker.synchronize_scheduled_inventory("alice", "periodic").facts["attempted"] == 0
        assert calls == []
        assert "PRIVATE-CORRUPT-TOKEN" not in repr(broker.synchronize_scheduled_inventory(
            "alice", "periodic",
        ))
    finally:
        service.storage.close()


def test_explicit_inventory_policy_never_mutates_materialization_permission(tmp_path):
    service, account, session, calls = _rig(tmp_path)
    try:
        broker = SynchronizationBrokerImpl(service)
        sid = broker._source_id(account)
        for mode in ("on_start", "periodic", "manual"):
            broker.set_inventory_refresh_schedule(sid, mode)
            assert service.storage.metadata_get(acquisition_policy_key(sid)) is None
        assert broker.inventory_refresh_schedule(sid)["mode"] == "manual"
        assert calls == []
    finally:
        service.storage.close()


def test_concurrent_due_triggers_are_singleflight_for_exact_source(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    service, account, _session, calls = _rig(tmp_path)
    try:
        broker = SynchronizationBrokerImpl(service)
        sid = broker._source_id(account)
        broker.set_inventory_refresh_schedule(sid, "on_start", interval_minutes=15)
        entered, release = Event(), Event()
        initial = service.synchronize_account_source

        def slow(*args, **kwargs):
            entered.set()
            assert release.wait(timeout=5)
            return initial(*args, **kwargs)

        service.synchronize_account_source = slow
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(broker.synchronize_scheduled_inventory, "alice", "startup")
            assert entered.wait(timeout=5)
            second = executor.submit(broker.synchronize_scheduled_inventory, "alice", "startup")
            result = second.result(timeout=5)
            assert result.facts["attempted"] == 0
            release.set()
            assert first.result(timeout=5).facts["attempted"] == 1
        assert len(calls) == 1
    finally:
        service.storage.close()


def test_clients_cannot_override_due_clock_to_bypass_refresh_throttle(tmp_path):
    service, account, _, calls = _rig(tmp_path)
    try:
        broker = SynchronizationBrokerImpl(service)
        sid = broker._source_id(account)
        broker.set_inventory_refresh_schedule(sid, "periodic", interval_minutes=10080)
        assert broker.synchronize_scheduled_inventory("alice", "periodic").facts["attempted"] == 1
        assert broker.synchronize_scheduled_inventory("alice", "periodic").facts["attempted"] == 0
        with pytest.raises(TypeError):
            broker.synchronize_scheduled_inventory(
                "alice", "periodic",
                now=datetime.now(timezone.utc) + timedelta(days=365),
            )
        assert len(calls) == 1
    finally:
        service.storage.close()

