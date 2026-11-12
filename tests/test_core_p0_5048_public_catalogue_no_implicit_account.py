"""P0 #5048: public catalogues never create a private account at config load.

Historical connections are user data: leave them intact, including their
identity association. A public catalogue notice grants no borrow/purchase right.
"""
from __future__ import annotations

import json

from cifereca.config import Config
from cifereca.config_defaults import DEFAULT_CONFIG


def test_fresh_config_keeps_public_catalogues_accountless(tmp_path):
    config = Config(tmp_path / "fresh.json")
    assert config.provider_accounts() == []
    assert "gallica_migration_031" not in DEFAULT_CONFIG["providers"]
    assert config.get("providers.gallica_migration_031") is None
    assert not (tmp_path / "fresh.json").exists()


def test_unmigrated_legacy_config_no_longer_synthesizes_public_account(tmp_path):
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps({
        "identities": [{"id": "reader-a", "label": "Reader A"}],
        "providers": {"accounts": [], "gallica_migration_031": False},
    }), encoding="utf-8")
    config = Config(path)
    assert config.provider_accounts() == []
    assert config.get("providers.gallica_migration_031") is False
    config.save()
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["providers"]["accounts"] == []
    assert stored["providers"]["gallica_migration_031"] is False


def test_existing_explicit_or_legacy_public_connection_remains_scoped(tmp_path):
    path = tmp_path / "accounts.json"
    accounts = [
        {
            "id": "gallica-public", "provider": "gallica", "label": "Legacy",
            "enabled": False, "anonymous": True, "credentials": {},
        },
        {
            "id": "public-b", "provider": "gallica", "identity_id": "reader-b",
            "label": "Explicit", "enabled": True, "anonymous": True,
            "credentials": {},
        },
    ]
    path.write_text(json.dumps({
        "identities": [
            {"id": "reader-a", "label": "Reader A"},
            {"id": "reader-b", "label": "Reader B"},
        ],
        "providers": {"accounts": accounts, "gallica_migration_031": True},
    }), encoding="utf-8")
    config = Config(path)
    rows = config.provider_accounts("gallica")
    assert len(rows) == 2
    assert rows[0]["identity_id"] == "reader-a"
    assert rows[0]["enabled"] is False
    assert rows[1]["identity_id"] == "reader-b"
    assert rows[1]["enabled"] is True
    assert config.get("providers.gallica_migration_031") is True
    config.save()
    reloaded = Config(path)
    assert reloaded.provider_accounts("gallica") == rows


def test_new_config_does_not_synthesize_any_private_right_or_permission(tmp_path):
    config = Config(tmp_path / "public.json")
    data = json.dumps(config.data, sort_keys=True).lower()
    assert "gallica-public" not in data
    assert "gallica_migration_031" not in data
    assert "sync.source." not in data
    assert "entitlement" not in data
