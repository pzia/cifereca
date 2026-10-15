"""P0 #1346: source/phase schedule admission is explicit, bounded, inert."""
import json
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace as NS

import pytest

from cifereca.core.source_refresh_policy import (
    SourceRefreshSchedule, decode_source_refresh_schedule,
    load_source_refresh_schedule, save_source_refresh_schedule,
    source_refresh_due,
)
from cifereca.storage import Storage

SID = "sync:" + "a" * 24
NOW = datetime(2026, 10, 15, 12, tzinfo=timezone.utc)


def due(schedule, trigger, **overrides):
    opts = dict(
        now=NOW, enabled=True, connected=True, capable=True,
        last_attempt="", phase_status="idle",
        already_requested_this_startup=False,
    )
    opts.update(overrides)
    return source_refresh_due(schedule, trigger=trigger, **opts)


def test_manual_defaults_never_schedule_network_or_acquisition():
    assert not due(SourceRefreshSchedule(), "startup")
    assert not due(SourceRefreshSchedule(), "periodic")
    assert decode_source_refresh_schedule(None) == SourceRefreshSchedule()
    assert decode_source_refresh_schedule("") == SourceRefreshSchedule()


def test_strict_source_opt_in_and_legacy_global_flag_not_migrated(tmp_path):
    storage = Storage(tmp_path / "source-policies.sqlite3")
    try:
        storage.metadata_set("kobo_backup.enabled", "true")
        storage.metadata_set("kobo_backup.interval_minutes", "15")
        assert load_source_refresh_schedule(storage, SID) == SourceRefreshSchedule()
        assert storage.metadata_get("sync.source.refresh.policy." + SID) is None
        configured = save_source_refresh_schedule(
            storage, SID, mode="periodic", interval_minutes=30,
        )
        assert configured == SourceRefreshSchedule("periodic", 30)
        assert load_source_refresh_schedule(storage, SID) == configured
        row = json.loads(storage.metadata_get("sync.source.refresh.policy." + SID))
        assert set(row) == {"schema", "mode", "interval_minutes"}
        assert "materialize" not in repr(row)
    finally:
        storage.close()


@pytest.mark.parametrize("raw", [
    '{"schema":"cifereca.source-refresh-policy/1","mode":"periodic","interval_minutes":true}',
    '{"schema":"cifereca.source-refresh-policy/1","mode":"periodic","interval_minutes":0}',
    '{"schema":"cifereca.source-refresh-policy/1","mode":"periodic","interval_minutes":10081}',
    '{"schema":"cifereca.source-refresh-policy/1","mode":"PERIODIC","interval_minutes":60}',
    '{"schema":"cifereca.source-refresh-policy/1","mode":"periodic","interval_minutes":60,"materialize":true}',
    '{"schema":"cifereca.source-refresh-policy/1","mode":"manual","interval_minutes":60,"mode":"periodic"}',
    '{"schema":"private-invalid","mode":"periodic","interval_minutes":60}',
    'true', '[]', '{"mode": "periodic"}', '{"credential":"secret"}', "x"*513,
])
def test_invalid_policy_never_grants_automatic_sync(raw):
    assert decode_source_refresh_schedule(raw) == SourceRefreshSchedule()


@pytest.mark.parametrize("invalid", [
    "sync:../private", "sync:ABCD", "kobo:secret", "", None,
])
def test_private_or_invalid_keys_never_write(tmp_path, invalid):
    storage = Storage(tmp_path / "unsafe-key.sqlite3")
    try:
        with pytest.raises(ValueError, match="invalid source refresh key"):
            save_source_refresh_schedule(storage, invalid, mode="on_start")
    finally:
        storage.close()


@pytest.mark.parametrize("mode,interval", [
    ("automatic", 60), ("materialize", 60), ("periodic", True),
    ("periodic", "15"), ("periodic", 10), ("periodic", 10081),
    (True, 60),
])
def test_invalid_or_download_like_policy_is_rejected_before_write(tmp_path, mode, interval):
    storage = Storage(tmp_path / "strict.sqlite3")
    try:
        with pytest.raises(ValueError):
            save_source_refresh_schedule(
                storage, SID, mode=mode, interval_minutes=interval,
            )
        assert storage.metadata_get("sync.source.refresh.policy." + SID) is None
    finally:
        storage.close()


def test_startup_runs_only_once_and_preserves_inventory_attempt_backoff():
    start = SourceRefreshSchedule("on_start", 15)
    assert due(start, "startup")
    assert not due(start, "periodic")
    assert not due(start, "startup", already_requested_this_startup=True)
    recent = (NOW - timedelta(minutes=5)).isoformat()
    assert not due(start, "startup", last_attempt=recent)
    older = (NOW - timedelta(minutes=25)).isoformat()
    assert due(start, "startup", last_attempt=older)


def test_periodic_refresh_only_after_interval_without_fabricating_stale_clock():
    policy = SourceRefreshSchedule("periodic", 45)
    assert not due(policy, "startup")
    assert due(policy, "periodic")
    assert not due(policy, "periodic", last_attempt=(NOW-timedelta(minutes=44)).isoformat())
    assert due(policy, "periodic", last_attempt=(NOW-timedelta(minutes=45)).isoformat())
    assert not due(policy, "periodic", last_attempt=(NOW+timedelta(seconds=1)).isoformat())
    assert not due(policy, "periodic", last_attempt="https://private/?token=SECRET")
    assert not due(policy, "periodic", last_attempt="2030-10-15T10:00:00+00:00")


@pytest.mark.parametrize("flags", [
    {"enabled": False}, {"connected": False}, {"capable": False},
    {"enabled": "true"}, {"connected": 1}, {"capable": None},
    {"phase_status": "running"},
])
def test_disabled_disconnected_busy_or_malformed_source_is_never_due(flags):
    assert not due(SourceRefreshSchedule("periodic", 15), "periodic", **flags)


def test_policy_read_is_inert_even_with_private_corrupt_storage(tmp_path):
    storage = Storage(tmp_path / "corrupt.sqlite3")
    try:
        storage.metadata_set("sync.source.refresh.policy." + SID,
                             '{"mode":"periodic","token":"PRIVATE"}')
        assert load_source_refresh_schedule(storage, SID) == SourceRefreshSchedule()
    finally:
        storage.close()
