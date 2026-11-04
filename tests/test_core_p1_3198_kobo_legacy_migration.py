"""P1 #3198: Kobo global preferences become rollback-only, scoped retry is durable.

Synthetic configuration and metadata only. Never performs supplier I/O,
materialization, filesystem acquisitions or changes to source consent.
"""
from __future__ import annotations

from copy import deepcopy
import json
from types import SimpleNamespace as NS

import pytest

from cifereca.config import Config
from cifereca.core import source_materialization_schedule as schedule
from cifereca.core.kobo_backup import _kobo_checkpoint_for


class Metadata:
    def __init__(self):
        self.rows = {}
        self.fail = None

    def metadata_get(self, key):
        return self.rows.get(key)

    def metadata_set(self, key, value):
        if key == self.fail:
            raise OSError("injected SQLite write refusal")
        self.rows[key] = value


def _account(source_id, identity):
    return NS(
        source_id=source_id, identity_id=identity, credential_id="credential-" + identity,
        provider_id="kobo", unique_id="borrower-" + identity,
    )


@pytest.fixture
def clock(monkeypatch):
    now = [100_000]
    monkeypatch.setattr(schedule, "_account_id", lambda account: account.source_id)
    monkeypatch.setattr(schedule.time, "time", lambda: now[0])
    # A real empty local backup checkpoint permits a first explicit attempt.
    monkeypatch.setattr("cifereca.core.kobo_backup._kobo_checkpoint_for", lambda *_: None)
    return now


def _legacy_config(path, *, enabled=True):
    accounts = [
        dict(id="kobo-a", provider="kobo", identity_id="a", enabled=True,
             credentials={}, options={}),
        dict(id="kobo-b", provider="kobo", identity_id="b", enabled=True,
             credentials={}, options={}),
        dict(id="numilog-b", provider="numilog", identity_id="b",
             enabled=True, credentials={}, options={}),
    ]
    old = {
        "enabled": enabled, "allow_unlinked_acquisition": True,
        "interval_minutes": 180, "directory": "legacy-kobo",
        "format": "epub", "display_profile": "Desktop",
        "future_extension": {"must": "survive"},
    }
    path.write_text(json.dumps({
        "identities": [
            {"id": "a", "label": "A", "directory_name": "a"},
            {"id": "b", "label": "B", "directory_name": "b"},
        ],
        "providers": {"accounts": accounts, "gallica_migration_031": True},
        "kobo_backup": old,
    }), encoding="utf-8")
    return old


def test_new_installation_has_no_global_kobo_defaults_or_migration(tmp_path):
    config = Config(tmp_path / "new.json")
    assert config.get("kobo_backup") is None
    assert config.get("migration_history.kobo_backup_globals_v1") is None
    assert "sync" not in config.data
    config.save()
    restored = Config(config.path)
    assert restored.get("kobo_backup") is None
    assert restored.get("migration_history.kobo_backup_globals_v1") is None


@pytest.mark.parametrize("enabled", [True, False, "true", 1, None])
def test_legacy_snapshot_is_versioned_exact_non_mutating_and_idempotent(
    tmp_path, enabled,
):
    path = tmp_path / "existing.json"
    old = _legacy_config(path, enabled=enabled)
    config = Config(path)
    snapshot = config.get("migration_history.kobo_backup_globals_v1")
    assert snapshot == {
        "schema_version": 1,
        "preferences": {key: old[key] for key in (
            "enabled", "allow_unlinked_acquisition", "interval_minutes",
            "directory", "display_profile", "format",
        )},
    }
    # Original fields remain intact for bounded rollback and old manual
    # downloads. Neither snapshot nor flags become a scoped source switch.
    assert config.get("kobo_backup") == old
    assert config.provider_accounts()[2]["provider"] == "numilog"
    assert not any("sync.source." in key for key in config.data)
    initial = deepcopy(config.data)
    config.save()
    reloaded = Config(path)
    assert reloaded.data == initial
    assert reloaded.get("kobo_backup") == old
    # An unrelated edit must not rewrite the first-seen rollback evidence.
    reloaded.set("kobo_backup.enabled", False)
    reloaded.save()
    changed = Config(path)
    assert changed.get("migration_history.kobo_backup_globals_v1") == snapshot
    assert changed.get("kobo_backup.enabled") is False


def test_invalid_or_existing_migration_metadata_is_never_overwritten(tmp_path):
    path = tmp_path / "existing.json"
    path.write_text(json.dumps({
        "kobo_backup": {"enabled": True, "interval_minutes": 120},
        "migration_history": {"kobo_backup_globals_v1": {
            "schema_version": 1, "preferences": {"enabled": False},
        }},
    }), encoding="utf-8")
    config = Config(path)
    assert config.get("migration_history.kobo_backup_globals_v1") == {
        "schema_version": 1, "preferences": {"enabled": False},
    }
    assert config.get("kobo_backup.enabled") is True
    path.write_text(json.dumps({
        "kobo_backup": {"enabled": True}, "migration_history": "legacy-extension",
    }), encoding="utf-8")
    assert Config(path).get("migration_history") == "legacy-extension"


def test_scoped_attempt_freezes_legacy_retry_without_granting_consent(
    clock, monkeypatch,
):
    storage = Metadata()
    old = [180]
    service = NS(
        storage=storage, kobo_backup_interval_minutes=lambda: old[0],
        _kobo_backup_account_due=lambda _credential: True,
    )
    alice = _account("source-alice", "alice")
    bob = _account("source-bob", "bob")
    # First attempt never depends on the legacy ENABLE flag or another
    # source's clock. Only the existing caller's source-policy gate dispatches.
    assert schedule.on_start_due(service, alice)
    assert schedule.on_start_due(service, bob)
    assert schedule.mark_on_start_attempt(service, alice)
    assert storage.rows[schedule.retry_interval_key("source-alice")] == "180"
    assert storage.rows["sync.source.materialization.last_attempt.source-alice"] == "100000"
    assert schedule.retry_interval_key("source-bob") not in storage.rows

    # Editing the retired global value does not shorten Alice's saved clock,
    # and Bob independently gets the new conservative 60-minute baseline.
    old[0] = 15
    assert schedule.mark_on_start_attempt(service, bob)
    assert storage.rows[schedule.retry_interval_key("source-bob")] == "60"
    clock[0] += 90 * 60
    assert schedule.on_start_due(service, alice) is False
    assert schedule.on_start_due(service, bob) is True
    clock[0] += 91 * 60
    assert schedule.on_start_due(service, alice) is True


@pytest.mark.parametrize("corrupt", [
    "0", "15", "59", "60000", "-60", "+60", "0x60", "60 ", "",
    "１２0", True, 60, None,
])
def test_malformed_scoped_cadence_never_weakens_retry_admission(
    clock, corrupt,
):
    storage = Metadata()
    account = _account("source", "alice")
    service = NS(
        storage=storage, kobo_backup_interval_minutes=lambda: 60,
        _kobo_backup_account_due=lambda *_: True,
    )
    if corrupt is not None:
        storage.rows[schedule.retry_interval_key("source")] = corrupt
        assert schedule.on_start_due(service, account) is False
        assert schedule.mark_on_start_attempt(service, account) is False
        assert "sync.source.materialization.last_attempt.source" not in storage.rows
    else:
        # A missing exact-source cadence is a safe first-use migration, not
        # a malformed explicit value.
        assert schedule.on_start_due(service, account)


@pytest.mark.parametrize("fail_kind", ["retry", "clock"])
def test_failed_metadata_commit_refuses_startup_dispatch(
    clock, fail_kind,
):
    storage = Metadata()
    account = _account("source", "alice")
    service = NS(
        storage=storage, kobo_backup_interval_minutes=lambda: 120,
        _kobo_backup_account_due=lambda *_: True,
    )
    storage.fail = (
        schedule.retry_interval_key("source") if fail_kind == "retry"
        else "sync.source.materialization.last_attempt.source"
    )
    assert schedule.mark_on_start_attempt(service, account) is False
    assert storage.metadata_get("sync.source.materialization.last_attempt.source") is None


def test_resumed_checkpoint_uses_pinned_scoped_window_not_global(
    clock, monkeypatch,
):
    from cifereca.core.kobo_backup import _kobo_backup_due

    storage = Metadata()
    legacy = [180]
    account = _account("source-alice", "alice")
    checkpoint = {"status": "complete", "updated_at": clock[0]}
    monkeypatch.setattr(
        "cifereca.core.kobo_backup._kobo_checkpoint_for",
        lambda *_args: checkpoint,
    )
    service = NS(
        storage=storage,
        config=NS(get=lambda key, default=None: legacy[0]),
        kobo_backup_interval_minutes=lambda: legacy[0],
        identity_for_connection=lambda provider, credential: NS(identity_id="alice"),
    )
    service._kobo_backup_account_due = lambda credential, *, interval_minutes=None: (
        _kobo_backup_due(
            service, credential_id=credential, interval_minutes=interval_minutes,
        )
    )
    assert schedule.mark_on_start_attempt(service, account)
    assert storage.metadata_get(schedule.retry_interval_key("source-alice")) == "180"

    # Both thresholds matter: 200 min since the scoped attempt but only
    # 80 min since a completed checkpoint. The retired global value of 15
    # would admit this old checkpoint; the source-pinned 180 must not.
    clock[0] += 200 * 60
    checkpoint["updated_at"] = clock[0] - 80 * 60
    legacy[0] = 15
    assert service._kobo_backup_account_due("credential-alice") is True
    assert schedule.on_start_due(service, account) is False
    checkpoint["updated_at"] = clock[0] - 190 * 60
    assert schedule.on_start_due(service, account) is True


def test_invalid_explicit_checkpoint_interval_is_never_due(clock, monkeypatch):
    from cifereca.core.kobo_backup import _kobo_backup_due

    checkpoint = {"status": "complete", "updated_at": 1}
    monkeypatch.setattr(
        "cifereca.core.kobo_backup._kobo_checkpoint_for",
        lambda *_args: checkpoint,
    )
    service = NS(
        config=NS(get=lambda *_: 1),
        identity_for_connection=lambda *_: NS(identity_id="alice"),
    )
    for invalid in (0, 15, True, "60", 999999):
        assert _kobo_backup_due(
            service, credential_id="credential-alice", interval_minutes=invalid,
        ) is False


def test_oversized_legacy_cadence_fails_closed_without_truncation(clock):
    account = _account("source", "alice")
    service = NS(
        storage=Metadata(), kobo_backup_interval_minutes=lambda: 99999999,
        _kobo_backup_account_due=lambda *_: True,
    )
    assert schedule.on_start_due(service, account) is False
    assert schedule.mark_on_start_attempt(service, account) is False
