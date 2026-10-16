"""#1346: all public sync-state fields redact untrusted legacy SQLite metadata."""
from __future__ import annotations

import json
import pytest

from cifereca.brokers import SynchronizationBrokerImpl
from cifereca.storage import Storage
from cifereca.gui.sync_view import sync_source_presentation
from tests.test_core_0978_source_state import fake_broker


PRIVATE = "https://private.invalid/order?auth=VERY_PRIVATE_ORDER /home/user/secret.epub"


@pytest.mark.parametrize("mode", ["json", "legacy_repr"])
def test_corrupted_status_timestamp_and_scope_never_leave_sync_broker(tmp_path, mode):
    storage = Storage(tmp_path / "untrusted-sync-state.sqlite3")
    try:
        broker = fake_broker(storage)
        source_id = broker.sources()[0].source_id
        key = "sync.source.state." + source_id
        payload = {
            "status": PRIVATE, "error": PRIVATE,
            "last_attempt": PRIVATE, "last_success": PRIVATE,
            "scope": PRIVATE,
        }
        storage.metadata_set(
            key, json.dumps(payload) if mode == "json" else str(payload),
        )
        public = broker.sources()[0]
        assert public.status == "unknown"
        assert public.error == "Échec de synchronisation"
        assert public.last_attempt == public.last_success == ""
        assert PRIVATE not in repr(public)
        assert PRIVATE not in repr(public.phase_states)
        assert PRIVATE not in sync_source_presentation(public).summary
        state = broker._state(source_id)
        assert state["scope"] == ""
        assert PRIVATE not in repr(state)
        if mode == "legacy_repr":
            assert PRIVATE not in storage.metadata_get(key)
        # The read is passive: no provider calls and no timestamp promoted
        # into confirmed success by unknown/corrupted metadata.
        assert public.phase_states[0].last_success == ""
    finally:
        storage.close()


def test_valid_confirmed_times_and_status_survive_read_without_rollback(tmp_path):
    storage = Storage(tmp_path / "valid-sync-state.sqlite3")
    try:
        broker = fake_broker(storage)
        source_id = broker.sources()[0].source_id
        before = "2026-10-15T10:15:00+00:00"
        storage.metadata_set(
            "sync.source.state." + source_id,
            json.dumps({
                "last_attempt": before, "last_success": before,
                "status": "ok", "error": "", "scope": "manual",
            }),
        )
        projected = broker.sources()[0]
        assert projected.status == "ok"
        assert projected.last_attempt == projected.last_success == before
        assert projected.error == ""
        assert projected.phase_states[0].status == "success"
        assert projected.phase_states[0].last_success == before
    finally:
        storage.close()


@pytest.mark.parametrize("invalid", [
    "2026-10-15T10:15:00",  # missing timezone
    "1999-01-01T10:15:00+00:00",  # outside accepted bounded epoch
    PRIVATE,
    "2017-01-01T10:00:00+00:00" + PRIVATE,
    "2050-10-15T10:15:00+00:00" + PRIVATE,
    "x" * 1000,
])
def test_invalid_last_success_never_claims_sync_success(tmp_path, invalid):
    storage = Storage(tmp_path / "invalid-sync-clock.sqlite3")
    try:
        broker = fake_broker(storage)
        source_id = broker.sources()[0].source_id
        key = "sync.source.state." + source_id
        storage.metadata_set(key, json.dumps({
            "last_attempt": invalid, "last_success": invalid, "status": "ok",
        }))
        state = broker.sources()[0]
        assert state.last_attempt == state.last_success == ""
        assert PRIVATE not in repr(state)
        assert state.phase_states[0].last_attempt == ""
        assert state.phase_states[0].last_success == ""
    finally:
        storage.close()
