"""P1 #1346: distinct read-only source phase states and conservative Kobo evidence."""
from __future__ import annotations

import json

from cifereca.core.kobo_backup import _kobo_checkpoint_key
from cifereca.plugin_api import SyncPhaseState
from tests.test_core_1346_source_preflight import _broker


def _phases(broker, identity="reader-2"):
    source = next(
        item for item in broker.sources(identity) if item.provider_id == "kobo"
    )
    return source, {item.phase: item for item in source.phase_states}


def test_source_phase_contract_does_not_migrate_or_acquire():
    service, broker, _target, calls = _broker()
    source, phases = _phases(broker)
    assert isinstance(phases["discover_rights"], SyncPhaseState)
    assert phases["discover_rights"].status == "idle"
    assert phases["materialize"].status == "idle"
    assert phases["reconcile"].status == "not_supported"
    other = next(item for item in broker.sources("alice") if item.provider_id != "kobo")
    assert {p.phase: p.status for p in other.phase_states} == {
        "discover_rights": "idle", "materialize": "not_supported",
        "reconcile": "not_supported",
    }
    assert calls == [] and service.calls == []
    assert service.storage.values == {}


def test_inventory_success_survives_partial_kobo_materialization():
    service, broker, target, calls = _broker()
    broker._set_state(target.source_id, {
        "status": "ok", "last_attempt": "2026-10-14T04:00:00+00:00",
        "last_success": "2026-10-14T04:00:00+00:00",
        # A generic source success no longer proves Kobo discovery. The
        # private-library attempt has its own attested phase metadata.
        "inventory_status": "success",
        "inventory_last_attempt": "2026-10-14T04:00:00+00:00",
        "inventory_last_success": "2026-10-14T04:00:00+00:00",
    })
    checkpoint_key = _kobo_checkpoint_key("reader-2", "vault-secret")
    service.storage.metadata_set(checkpoint_key, json.dumps({
        "schema_version": 1, "status": "partial", "updated_at": 1791954000,
        "counts": {"downloaded": 5, "errors": 1},
        "last_item_digest": "PRIVATE_DIGEST",
        "item_id": "PRIVATE_BOOK", "token": "PRIVATE_KEY",
    }))
    before = dict(service.storage.values)
    source, phases = _phases(broker)
    assert source.last_success == "2026-10-14T04:00:00+00:00"
    assert phases["discover_rights"].status == "success"
    assert phases["discover_rights"].last_success == source.last_success
    assert phases["materialize"].status == "partial"
    assert phases["materialize"].last_attempt
    assert phases["materialize"].last_success == ""
    assert phases["reconcile"].status == "not_supported"
    for private in ("PRIVATE", "vault-secret", "reader-2"):
        assert private not in repr(source.phase_states)
    assert before == service.storage.values
    assert not service.calls and not calls


def test_complete_checkpoint_has_phase_success_but_is_not_permission():
    service, broker, _target, _calls = _broker()
    service.storage.metadata_set(
        _kobo_checkpoint_key("reader-2", "vault-secret"),
        json.dumps({"schema_version": 1, "status": "complete",
                    "updated_at": 1791954000}),
    )
    _source, phases = _phases(broker)
    assert phases["materialize"].status == "success"
    assert phases["materialize"].last_success == phases["materialize"].last_attempt
    assert phases["materialize"].last_success.endswith("+00:00")
    # A persisted checkpoint is not a new authorization or a scheduled task.
    assert phases["reconcile"].status == "not_supported"
    assert service.calls == []


def test_untrusted_checkpoint_and_timestamps_never_reach_public_snapshot():
    service, broker, target, calls = _broker()
    broker._set_state(target.source_id, {
        "status": "PRIVATE_STATUS", "last_attempt": "PRIVATE_TOKEN",
        "last_success": "2026-10-14T04:00:00+00:00?token=PRIVATE",
    })
    key = _kobo_checkpoint_key("reader-2", "vault-secret")
    for data in (
        {"schema_version": 1, "status": ["PRIVATE"], "updated_at": "PRIVATE"},
        {"schema_version": 1, "status": "complete", "updated_at": 9999999999999},
        {"schema_version": 1, "status": "complete", "updated_at": True},
    ):
        service.storage.metadata_set(key, json.dumps(data))
        _source, phases = _phases(broker)
        assert phases["discover_rights"].status == "idle"
        assert phases["discover_rights"].last_attempt == ""
        assert phases["discover_rights"].last_success == ""
        assert phases["materialize"].last_attempt == ""
        assert phases["materialize"].last_success == ""
        assert "PRIVATE" not in repr(tuple(phases.values()))
    assert calls == [] and service.calls == []


def test_wrong_identity_binding_does_not_expose_foreign_checkpoint():
    service, broker, _target, calls = _broker(broken_owner=True)
    service.storage.metadata_set(
        _kobo_checkpoint_key("reader-2", "vault-secret"),
        json.dumps({"schema_version": 1, "status": "complete",
                    "updated_at": 1791954000}),
    )
    _source, phases = _phases(broker)
    assert phases["materialize"].status == "unknown"
    assert phases["materialize"].last_attempt == ""
    assert not calls and not service.calls
