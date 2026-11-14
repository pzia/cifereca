"""P0 #5975: the 0.8.1 cardinality writer is a one-time data migration.

Old account rows are preserved and partitioned deterministically. Completed
migration history blocks any later automatic source-specific remapping. No
credential, private rights, sync consent or ADEPT grant is synthesized.
"""
from __future__ import annotations

from copy import deepcopy
import json

from cifereca.config import Config, _LEGACY_CARDINALITY_V1


def _old(path, *, history=None, provider="kobo"):
    data = {
        "identities": [{
            "id": "alice", "label": "Alice", "adept_profile": "ade-alice",
            "preferences": {
                "reading_languages": ["fr", "en"],
                "prefer_original_language": True,
            },
        }],
        "providers": {"accounts": [
            {"id": "source-a", "provider": provider, "identity_id": "alice",
             "label": "Source A", "enabled": True, "credentials": {}, "options": {}},
            {"id": "source-b", "provider": provider, "identity_id": "alice",
             "label": "Source B", "enabled": True, "credentials": {}, "options": {}},
        ]},
    }
    if history is not None:
        data["migration_history"] = history
    path.write_text(json.dumps(data), encoding="utf-8")
    return data


def test_old_duplicate_split_then_tombstone_survives_reload(tmp_path):
    path = tmp_path / "historical.json"
    original = _old(path)
    config = Config(path)
    accounts = config.provider_accounts("kobo")
    assert {row["id"] for row in accounts} == {"source-a", "source-b"}
    assert len({row["identity_id"] for row in accounts}) == 2
    assert len(config.identities()) == 2
    assert config.get(f"migration_history.{_LEGACY_CARDINALITY_V1}") == {
        "schema_version": 1, "status": "completed", "split_count": 1,
    }
    assert len([row for row in config.identities()
                if row.get("adept_profile") == "ade-alice"]) == 1
    assert "sync" not in config.data
    # Loading is in-memory. The existing atomic Config.save remains the only
    # mechanism for committing a config migration.
    assert json.loads(path.read_text(encoding="utf-8")) == original
    config.save()
    committed = deepcopy(config.data)
    loaded = Config(path)
    assert loaded.data == committed
    assert loaded.provider_accounts("kobo") == accounts


def test_later_duplicate_is_not_silently_assigned_to_new_profile(tmp_path):
    path = tmp_path / "modern.json"
    _old(path)
    config = Config(path)
    config.save()
    before = config.identities()
    changed = config.provider_accounts()
    changed[1]["identity_id"] = "alice"
    config.set("providers.accounts", changed)
    config.save()
    loaded = Config(path)
    assert loaded.identities() == before
    assert [row["identity_id"] for row in loaded.provider_accounts("kobo")] == [
        "alice", "alice",
    ]
    assert loaded.get(f"migration_history.{_LEGACY_CARDINALITY_V1}")[
        "split_count"
    ] == 1


def test_foreign_and_completed_history_are_never_overwritten(tmp_path):
    path = tmp_path / "history.json"
    opaque = {"status": "inherited-unknown", "schema_version": 99}
    _old(path, history={
        _LEGACY_CARDINALITY_V1: opaque,
        "other_migration": {"private": "opaque"},
    })
    config = Config(path)
    assert config.get(f"migration_history.{_LEGACY_CARDINALITY_V1}") == opaque
    assert config.get("migration_history.other_migration") == {
        "private": "opaque",
    }
    assert len(config.identities()) == 1
    assert [a["identity_id"] for a in config.provider_accounts("kobo")] == [
        "alice", "alice",
    ]


def test_corrupted_history_denies_reassignment_and_does_not_crash(tmp_path):
    path = tmp_path / "history.json"
    _old(path, history="unknown-migration-format")
    loaded = Config(path)
    assert loaded.get("migration_history") == "unknown-migration-format"
    assert len(loaded.identities()) == 1
    assert all(a["identity_id"] == "alice"
               for a in loaded.provider_accounts("kobo"))


def test_non_kobo_historical_accounts_closed_without_source_rewrite(tmp_path):
    path = tmp_path / "future.json"
    _old(path, provider="future-source")
    loaded = Config(path)
    assert len(loaded.identities()) == 1
    assert loaded.get(f"migration_history.{_LEGACY_CARDINALITY_V1}") == {
        "schema_version": 1, "status": "completed", "split_count": 0,
    }
    assert all(a["identity_id"] == "alice"
               for a in loaded.provider_accounts("future-source"))


def test_new_empty_configuration_does_not_gain_legacy_migration_marker(tmp_path):
    loaded = Config(tmp_path / "fresh.json")
    assert loaded.get(f"migration_history.{_LEGACY_CARDINALITY_V1}") is None
    assert "sync" not in loaded.data
