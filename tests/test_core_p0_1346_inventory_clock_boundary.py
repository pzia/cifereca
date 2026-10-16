"""P0 #1346: opening loans cannot certify private Kobo inventory freshness.

All provider responses below are fakes. No network, acquisition or ADEPT I/O.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from types import SimpleNamespace as NS

from cifereca.brokers import SynchronizationBrokerImpl
from cifereca.gui.source_inventory_timer import periodic_identities_due
from tests.test_core_p0_1346_inventory_refresh_broker import _rig


def _when():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _probe(service, broker):
    return NS(
        _closed=False,
        service=NS(identities=[NS(identity_id="alice")]),
        plugin_manager=NS(broker=lambda _name: broker),
    )


def _discover_phase(broker, source_id):
    source = next(row for row in broker.sources("alice") if row.source_id == source_id)
    return source, next(p for p in source.phase_states if p.phase == "discover_rights")


def test_opening_loan_sync_does_not_throttle_opted_in_kobo_inventory(tmp_path):
    service, account, _session, calls = _rig(tmp_path)
    try:
        broker = SynchronizationBrokerImpl(service)
        sid = broker._source_id(account)
        broker.set_inventory_refresh_schedule(sid, "periodic", interval_minutes=120)
        broker.record_opening_state(
            sid, identity_id="alice", status="success", attempted_at=_when(),
        )
        source, inventory = _discover_phase(broker, sid)
        assert source.status == "success"
        assert source.last_attempt
        assert inventory.status == "idle"
        assert inventory.last_attempt == ""
        assert inventory.last_success == ""
        assert periodic_identities_due(_probe(service, broker), broker) == ("alice",)

        scheduled = broker.synchronize_scheduled_inventory("alice", "periodic")
        assert scheduled.ok and scheduled.facts["attempted"] == 1
        assert len(calls) == 1
        source, inventory = _discover_phase(broker, sid)
        assert inventory.status == "success"
        assert inventory.last_attempt and inventory.last_success
        assert periodic_identities_due(_probe(service, broker), broker) == ()
        assert broker.synchronize_scheduled_inventory("alice", "periodic").facts[
            "attempted"
        ] == 0
    finally:
        service.storage.close()


def test_later_opening_projection_never_resets_actual_kobo_inventory_clock(tmp_path):
    service, account, _session, calls = _rig(tmp_path)
    try:
        broker = SynchronizationBrokerImpl(service)
        sid = broker._source_id(account)
        broker.set_inventory_refresh_schedule(sid, "periodic", interval_minutes=120)
        assert broker.synchronize_scheduled_inventory("alice", "periodic").facts[
            "attempted"
        ] == 1
        earlier = broker._state(sid)
        broker.record_opening_state(
            sid, identity_id="alice", status="failed",
            attempted_at="2099-01-01T00:00:00+00:00",
        )
        final = broker._state(sid)
        assert final["inventory_last_attempt"] == earlier["inventory_last_attempt"]
        assert final["inventory_last_success"] == earlier["inventory_last_success"]
        assert final["inventory_status"] == "success"
        source, phase = _discover_phase(broker, sid)
        assert phase.status == "success"
        assert phase.last_success == earlier["inventory_last_success"]
        assert periodic_identities_due(_probe(service, broker), broker) == ()
    finally:
        service.storage.close()


def test_failed_inventory_still_throttles_but_does_not_claim_success(tmp_path):
    service, account, _session, calls = _rig(tmp_path)
    try:
        broker = SynchronizationBrokerImpl(service)
        sid = broker._source_id(account)
        broker.set_inventory_refresh_schedule(sid, "periodic", interval_minutes=120)
        service.synchronize_account_source = lambda a, **kwargs: (
            calls.append(("failed", a.credential_id)) or {"library": 1, "warnings": 1}
        )
        answer = broker.synchronize_scheduled_inventory("alice", "periodic")
        assert not answer.ok and answer.facts["attempted"] == 1
        assert answer.facts["failed"] == 1
        source, phase = _discover_phase(broker, sid)
        assert phase.status == "partial"
        assert phase.last_attempt and not phase.last_success
        assert periodic_identities_due(_probe(service, broker), broker) == ()
        assert len(calls) == 1
    finally:
        service.storage.close()


def test_invalid_inventory_clock_blocks_automatic_dispatch_fail_closed(tmp_path):
    service, account, _session, calls = _rig(tmp_path)
    try:
        broker = SynchronizationBrokerImpl(service)
        sid = broker._source_id(account)
        broker.set_inventory_refresh_schedule(sid, "periodic", interval_minutes=15)
        service.storage.metadata_set("sync.source.state." + sid, json.dumps({
            "scope": "opening", "status": "success",
            "inventory_status": "success",
            "inventory_last_attempt": "https://secret.invalid/?bearer=PRIVATE",
        }))
        source, phase = _discover_phase(broker, sid)
        assert phase.status == "unknown"
        assert "PRIVATE" not in repr(source)
        assert broker.synchronize_scheduled_inventory("alice", "periodic").facts[
            "attempted"
        ] == 0
        assert periodic_identities_due(_probe(service, broker), broker) == ()
        assert calls == []
    finally:
        service.storage.close()


def test_running_opening_phase_prevents_concurrent_inventory_even_without_clock(tmp_path):
    service, account, _session, calls = _rig(tmp_path)
    try:
        broker = SynchronizationBrokerImpl(service)
        sid = broker._source_id(account)
        broker.set_inventory_refresh_schedule(sid, "periodic", interval_minutes=15)
        service.storage.metadata_set("sync.source.state." + sid, json.dumps({
            "scope": "opening", "status": "running", "last_attempt": _when(),
        }))
        assert broker.synchronize_scheduled_inventory("alice", "periodic").facts[
            "attempted"
        ] == 0
        assert periodic_identities_due(_probe(service, broker), broker) == ()
        assert calls == []
    finally:
        service.storage.close()
