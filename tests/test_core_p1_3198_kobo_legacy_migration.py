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


@pytest.mark.parametrize("account_id", ["source-alice", "source-bob"])
def test_retired_w2_startup_never_dispatches_or_mutates(account_id):
    """Previously allowed retry scheduling is now intentionally disabled."""
    storage = Metadata()
    storage.rows[schedule.policy_key(account_id)] = "1"
    storage.rows[schedule.retry_interval_key(account_id)] = "60"
    before = deepcopy(storage.rows)
    service = NS(storage=storage)
    account = _account(account_id, "alice")
    assert schedule.can_enable_kobo_on_start(service, account) is False
    assert schedule.on_start_due(service, account) is False
    assert schedule.mark_on_start_attempt(service, account) is False
    assert schedule.kobo_on_start_sessions(service) == ()
    assert storage.rows == before


@pytest.mark.parametrize("invalid", [None, "", "0", "15", "59", "60000",
                                     "-60", "+60", True, 60, "120"])
def test_legacy_retry_metadata_cannot_authorize_retired_w2(invalid):
    storage = Metadata()
    account = _account("source", "alice")
    if invalid is not None:
        storage.rows[schedule.retry_interval_key("source")] = invalid
    before = deepcopy(storage.rows)
    service = NS(storage=storage)
    assert schedule.on_start_due(service, account) is False
    assert schedule.mark_on_start_attempt(service, account) is False
    assert storage.rows == before


def test_retired_w2_legacy_keys_still_address_historical_values():
    assert schedule.policy_key("source") == "sync.source.materialization.on_start.source"
    assert schedule.retry_interval_key("source") == "sync.source.materialization.retry_minutes.source"
