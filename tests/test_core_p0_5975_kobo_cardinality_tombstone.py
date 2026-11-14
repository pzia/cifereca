"""#5975: the historical Kobo cardinality writer is a one-time migration only.

No plugin or provider I/O, no SQLite/EPUB mutation, no ADEPT grant and no
source-consent inference. New settings are validated by generic admission.
"""
from __future__ import annotations

from copy import deepcopy
import json

from cifereca.config import Config
from cifereca.legacy_migrations.kobo_account_cardinality_v1 import MIGRATION_ID


def _legacy(path, *, history=None):
    data = {
        "identities": [{
            "id": "family", "label": "Famille",
            "directory_name": "family", "adept_profile": "ade-family",
        }],
        "providers": {"accounts": [
            {"id": "vault-a", "provider": "kobo", "identity_id": "family",
             "label": "Vault A", "enabled": True, "credentials": {}, "options": {}},
            {"id": "vault-b", "provider": "kobo", "identity_id": "family",
             "label": "Vault B", "enabled": True, "credentials": {}, "options": {}},
            {"id": "other", "provider": "fictional", "identity_id": "family",
             "enabled": True, "credentials": {}, "options": {}},
        ]},
    }
    if history is not None:
        data["migration_history"] = history
    path.write_text(json.dumps(data), encoding="utf-8")
    return data


def test_historical_duplicate_is_split_once_and_tombstoned(tmp_path):
    path = tmp_path / "legacy.json"
    before = _legacy(path)
    config = Config(path)
    rows = config.provider_accounts()
    ids = {row["identity_id"] for row in rows if row["provider"] == "kobo"}
    assert len(ids) == 2
    assert rows[2]["identity_id"] == "family"
    assert rows[2]["provider"] == "fictional"
    assert config.get("migration_history." + MIGRATION_ID) == {
        "schema_version": 1, "status": "completed", "split_count": 1,
    }
    assert config.identity("family")["adept_profile"] == "ade-family"
    assert all(not row.get("adept_profile") for row in config.identities()
               if row["id"] != "family")
    assert "sync" not in config.data
    assert path.read_text(encoding="utf-8") == json.dumps(before)
    config.save()
    snapshot = deepcopy(config.data)
    second = Config(path)
    assert second.data == snapshot
    assert second.provider_accounts() == rows
    assert "sync" not in second.data


def test_completed_migration_does_not_repair_later_manual_duplicate(tmp_path):
    path = tmp_path / "existing.json"
    _legacy(path)
    config = Config(path)
    config.save()
    # A later invalid manual edit must be handled by source-cardinality
    # validation, not silently attributed to a newly created identity.
    config = Config(path)
    before = config.identities()
    rows = config.provider_accounts()
    rows[1]["identity_id"] = "family"
    config.set("providers.accounts", rows)
    config.save()
    loaded = Config(path)
    assert loaded.identities() == before
    assert [row["identity_id"] for row in loaded.provider_accounts("kobo")] == [
        "family", "family",
    ]
    assert loaded.get("migration_history." + MIGRATION_ID)["split_count"] == 1


def test_preexisting_unknown_tombstone_is_preserved_and_blocks_repair(tmp_path):
    path = tmp_path / "existing.json"
    marker = {"schema_version": 99, "status": "unknown"}
    data = _legacy(path, history={MIGRATION_ID: marker, "other_source": {"opaque": True}})
    config = Config(path)
    assert config.get("migration_history." + MIGRATION_ID) == marker
    assert config.get("migration_history.other_source") == {"opaque": True}
    assert [r["identity_id"] for r in config.provider_accounts("kobo")] == [
        "family", "family",
    ]
    assert len(config.identities()) == 1
    assert "sync" not in config.data


def test_new_and_unrelated_provider_configs_have_no_kobo_migration_marker(tmp_path):
    new = Config(tmp_path / "fresh.json")
    assert new.get("migration_history." + MIGRATION_ID) is None
    path = tmp_path / "other.json"
    path.write_text(json.dumps({
        "providers": {"accounts": [
            {"id": "future", "provider": "fictional", "identity_id": "default"},
        ]},
    }), encoding="utf-8")
    other = Config(path)
    assert other.get("migration_history." + MIGRATION_ID) is None


def test_invalid_migration_history_is_never_overwritten_or_activated(tmp_path):
    path = tmp_path / "bad-history.json"
    _legacy(path, history="opaque-legacy")
    config = Config(path)
    assert config.get("migration_history") == "opaque-legacy"
    assert len(config.identities()) == 1
    assert len({row["identity_id"] for row in config.provider_accounts("kobo")}) == 1
    assert "sync" not in config.data
