"""Numilog P0 #1905: private purchased-inventory scheduling, never backup."""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace as NS

import pytest

from cifereca.brokers import SynchronizationBrokerImpl
from cifereca.core.source_refresh_policy import numilog_inventory_source_eligible
from cifereca.models import Account
from tests.test_core_p0_1346_inventory_refresh_broker import _rig


def _numilog(tmp_path):
    """Use the real broker/storage rig, with one synthetic purchased-store account."""
    service, account, session, calls = _rig(tmp_path)
    account.provider_id = "numilog"
    account.provider_label = "Numilog"
    account.credential_id = "fake-numilog-credential"
    account.unique_id = "fake-numilog-account"
    session.provider_id = "numilog"
    session.credential_id = account.credential_id
    session.capabilities.direct_download = True  # not inventory consent
    service._sessions = {"numilog::fake-numilog-credential": session}
    service.storage.provider_connection_put({
        "identity_id": "alice", "provider_id": "numilog",
        "credential_id": account.credential_id, "enabled": True,
    })
    service.identity_by_id = lambda identifier: (
        NS(identity_id=identifier) if identifier in {"alice", "bob"} else None
    )
    service.identities = [NS(identity_id="alice"), NS(identity_id="bob")]
    return service, account, session, calls


def _phase(broker, sid):
    source = next(row for row in broker.sources("alice") if row.source_id == sid)
    return next(row for row in source.phase_states if row.phase == "discover_rights")


def test_opening_success_cannot_certify_or_throttle_numilog_inventory(tmp_path):
    service, account, _, calls = _numilog(tmp_path)
    try:
        broker = SynchronizationBrokerImpl(service)
        sid = broker._source_id(account)
        broker.set_inventory_refresh_schedule(sid, "periodic", interval_minutes=120)
        broker.record_opening_state(
            sid, identity_id="alice", status="success",
            attempted_at=datetime.now(timezone.utc).isoformat(),
        )
        phase = _phase(broker, sid)
        assert phase.status == "idle"
        assert not phase.last_attempt and not phase.last_success
        assert broker.synchronize_scheduled_inventory("alice", "periodic").facts["attempted"] == 1
        phase = _phase(broker, sid)
        assert phase.status == "success" and phase.last_attempt and phase.last_success
        assert len(calls) == 1
        broker.record_opening_state(
            sid, identity_id="alice", status="failed",
            attempted_at="2099-01-01T00:00:00+00:00",
        )
        assert _phase(broker, sid) == phase
        assert broker.synchronize_scheduled_inventory("alice", "periodic").facts["attempted"] == 0
        assert len(calls) == 1
    finally:
        service.storage.close()


@pytest.mark.parametrize("outcome", ["partial", "failed", "invalid"])
def test_numilog_inventory_failure_preserves_only_attested_success(tmp_path, outcome):
    service, account, _, calls = _numilog(tmp_path)
    try:
        broker = SynchronizationBrokerImpl(service)
        sid = broker._source_id(account)
        assert broker.synchronize_source(sid).ok
        previous = _phase(broker, sid).last_success
        def fail(*args, **kwargs):
            calls.append("failure")
            if outcome == "failed":
                raise RuntimeError("https://private.invalid/?auth=SECRET")
            return {"library": 1, "warnings": 1} if outcome == "partial" else None
        service.synchronize_account_source = fail
        assert not broker.synchronize_source(sid).ok
        phase = _phase(broker, sid)
        assert phase.status == ("partial" if outcome == "partial" else "failed")
        assert phase.last_attempt and phase.last_success == previous
        assert "SECRET" not in repr(broker.sources("alice"))
        broker.set_inventory_refresh_schedule(sid, "periodic", interval_minutes=120)
        assert broker.synchronize_scheduled_inventory("alice", "periodic").facts["attempted"] == 0
        assert len(calls) == 2
    finally:
        service.storage.close()


def test_numilog_damaged_inventory_clock_blocks_automatic_retry(tmp_path):
    service, account, _, calls = _numilog(tmp_path)
    try:
        broker = SynchronizationBrokerImpl(service)
        sid = broker._source_id(account)
        broker.set_inventory_refresh_schedule(sid, "on_start")
        service.storage.metadata_set("sync.source.state." + sid, json.dumps({
            "status": "success", "scope": "opening",
            "inventory_status": "success",
            "inventory_last_attempt": "https://private.invalid/?auth=SECRET",
        }))
        phase = _phase(broker, sid)
        assert phase.status == "unknown" and phase.last_attempt == ""
        assert "SECRET" not in repr(phase)
        assert broker.synchronize_scheduled_inventory("alice", "startup").facts["attempted"] == 0
        assert calls == []
    finally:
        service.storage.close()


def test_numilog_manual_is_default_and_not_a_backup_permission(tmp_path):
    service, account, session, calls = _numilog(tmp_path)
    try:
        broker = SynchronizationBrokerImpl(service)
        sid = broker._source_id(account)
        assert numilog_inventory_source_eligible(service, account)
        assert broker.inventory_refresh_schedule(sid) == {
            "mode": "manual", "interval_minutes": 60,
        }
        service.storage.metadata_set("kobo_backup.enabled", "1")
        assert broker.synchronize_scheduled_inventory("alice", "startup").facts["attempted"] == 0
        assert calls == []
        assert broker.local_materialization_schedule(sid) == "not_supported"
        with pytest.raises(ValueError):
            broker.set_local_materialization_schedule(sid, "on_start")
        assert session.capabilities.direct_download is True
        assert service.storage.metadata_get("sync.source.materialization.on_start." + sid) is None
    finally:
        service.storage.close()


def test_numilog_opt_in_startup_only_refreshes_private_inventory_once(tmp_path):
    service, account, session, calls = _numilog(tmp_path)
    try:
        broker = SynchronizationBrokerImpl(service)
        sid = broker._source_id(account)
        assert broker.set_inventory_refresh_schedule(sid, "on_start", interval_minutes=15) == {
            "mode": "on_start", "interval_minutes": 15,
        }
        assert not calls  # saving a preference performs no remote work
        result = broker.synchronize_scheduled_inventory("alice", "startup")
        assert result.ok
        assert result.facts["phase"] == "discover_rights"
        assert result.facts["acquisition_authorized"] is False
        assert result.facts["attempted"] == 1
        assert len(calls) == 1
        assert calls[0][:2] == ("alice", account.credential_id)
        assert calls[0][2]["deep"] is False
        assert broker.synchronize_scheduled_inventory("alice", "startup").facts["attempted"] == 0
        assert SynchronizationBrokerImpl(service).synchronize_scheduled_inventory(
            "alice", "startup",
        ).facts["attempted"] == 0
        assert broker.synchronize_scheduled_inventory("bob", "startup").facts["attempted"] == 0
        assert len(calls) == 1
        assert broker.local_materialization_schedule(sid) == "not_supported"
    finally:
        service.storage.close()


def test_numilog_periodic_requires_explicit_tick_and_preserves_throttle(tmp_path):
    service, account, session, calls = _numilog(tmp_path)
    try:
        broker = SynchronizationBrokerImpl(service)
        sid = broker._source_id(account)
        broker.set_inventory_refresh_schedule(sid, "periodic", interval_minutes=30)
        assert broker.synchronize_scheduled_inventory("alice", "startup").facts["attempted"] == 0
        assert broker.synchronize_scheduled_inventory("alice", "periodic").facts["attempted"] == 1
        assert broker.synchronize_scheduled_inventory("alice", "periodic").facts["attempted"] == 0
        assert len(calls) == 1
        assert broker._state(sid)["last_success"]
        assert service.storage.metadata_get("sync.source.materialization.on_start." + sid) is None
    finally:
        service.storage.close()


@pytest.mark.parametrize("mutation", ["disconnect", "replace_session", "duplicate", "rebind", "disabled"])
def test_numilog_revalidation_refuses_stale_or_ambiguous_private_account(tmp_path, mutation):
    service, account, session, calls = _numilog(tmp_path)
    try:
        broker = SynchronizationBrokerImpl(service)
        sid = broker._source_id(account)
        broker.set_inventory_refresh_schedule(sid, "on_start")
        if mutation == "disconnect":
            session.connected = False
        elif mutation == "replace_session":
            service._sessions["numilog::fake-numilog-credential"] = NS(
                provider_id="numilog", credential_id=account.credential_id,
                connected=True, capabilities=session.capabilities,
            )
            # A sole *new* session may be valid; ambiguity is instead
            # simulated by leaving two current sessions with the credential.
            service._sessions["old"] = session
        elif mutation == "duplicate":
            service.accounts.append(Account(
                display_name="Other Numilog", unique_id="fake-other",
                provider_id="numilog", credential_id=account.credential_id,
                identity_id="bob",
            ))
        elif mutation == "rebind":
            service.storage.provider_connection_put({
                "identity_id": "bob", "provider_id": "numilog",
                "credential_id": account.credential_id, "enabled": True,
            })
        else:
            broker.set_enabled(sid, False)
        assert not numilog_inventory_source_eligible(service, account)
        assert broker.synchronize_scheduled_inventory("alice", "startup").facts["attempted"] == 0
        with pytest.raises(ValueError):
            broker.set_inventory_refresh_schedule(sid, "on_start")
        assert calls == []
        assert broker.set_inventory_refresh_schedule(sid, "manual")["mode"] == "manual"
    finally:
        service.storage.close()


def test_numilog_corrupt_preferences_and_running_phase_never_authorize(tmp_path):
    service, account, session, calls = _numilog(tmp_path)
    try:
        broker = SynchronizationBrokerImpl(service)
        sid = broker._source_id(account)
        key = "sync.source.refresh.policy." + sid
        service.storage.metadata_set(key, '{"schema":"broken","mode":"on_start","interval_minutes":15}')
        assert broker.inventory_refresh_schedule(sid)["mode"] == "manual"
        assert broker.synchronize_scheduled_inventory("alice", "startup").facts["attempted"] == 0
        broker.set_inventory_refresh_schedule(sid, "periodic", interval_minutes=15)
        state = {"last_attempt": (
            datetime.now(timezone.utc) - timedelta(days=2)
        ).isoformat(), "status": "running"}
        service.storage.metadata_set("sync.source.state." + sid, json.dumps(state))
        assert broker.synchronize_scheduled_inventory("alice", "periodic").facts["attempted"] == 0
        service.storage.metadata_set("sync.source.state." + sid, "{SECRET")
        assert broker.synchronize_scheduled_inventory("alice", "periodic").facts["attempted"] == 0
        assert calls == []
    finally:
        service.storage.close()


def test_numilog_is_scoped_to_each_consented_identity_not_all_accounts(tmp_path):
    service, account, session, calls = _numilog(tmp_path)
    try:
        second = Account(
            display_name="Numilog Bob", unique_id="fake-bob-account",
            provider_id="numilog", credential_id="fake-bob-credential",
            identity_id="bob",
        )
        service.accounts.append(second)
        second_session = NS(
            provider_id="numilog", credential_id=second.credential_id, connected=True,
            capabilities=NS(personal_library=True, direct_download=True),
        )
        service._sessions["numilog::fake-bob-credential"] = second_session
        service.storage.provider_connection_put({
            "identity_id": "bob", "provider_id": "numilog",
            "credential_id": second.credential_id, "enabled": True,
        })
        broker = SynchronizationBrokerImpl(service)
        first_sid, second_sid = broker._source_id(account), broker._source_id(second)
        broker.set_inventory_refresh_schedule(first_sid, "on_start")
        assert broker.synchronize_scheduled_inventory("bob", "startup").facts["attempted"] == 0
        assert broker.synchronize_scheduled_inventory("alice", "startup").facts["attempted"] == 1
        broker.set_inventory_refresh_schedule(second_sid, "on_start")
        assert broker.synchronize_scheduled_inventory("bob", "startup").facts["attempted"] == 1
        assert len(calls) == 2
        assert {row[:2] for row in calls} == {
            ("alice", "fake-numilog-credential"),
            ("bob", "fake-bob-credential"),
        }
        assert all(row[2]["deep"] is False for row in calls)
        assert all(row[2].get("control") is None for row in calls)
    finally:
        service.storage.close()
