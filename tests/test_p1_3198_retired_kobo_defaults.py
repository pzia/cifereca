"""P1 #3198: retire dead global Kobo defaults without altering legacy files.

Fresh Config files must not silently introduce a global acquisition policy.
Legacy keys remain round-trippable for rollback and must never authorize
canonical source synchronization, including after a reload/save cycle.
"""
from __future__ import annotations

import json

from cifereca.config import Config
from cifereca.config_defaults import DEFAULT_CONFIG


def test_fresh_configuration_does_not_inject_retired_kobo_defaults(tmp_path):
    assert "kobo_backup" not in DEFAULT_CONFIG
    target = tmp_path / "fresh.json"
    cfg = Config(target)
    assert cfg.get("kobo_backup") is None
    cfg.save()
    raw = json.loads(target.read_text(encoding="utf-8"))
    assert "kobo_backup" not in raw
    assert "kobo_backup" not in Config(target).data


def test_legacy_kobo_global_settings_survive_load_save_without_becoming_consent(tmp_path):
    target = tmp_path / "legacy.json"
    old = {
        "kobo_backup": {
            "enabled": True, "interval_minutes": 15,
            "directory": "synthetic-old-dir", "display_profile": "Desktop",
            "allow_unlinked_acquisition": True,
            "future_unknown_option": "preserve-for-rollback",
        },
        "providers": {"accounts": []},
    }
    target.write_text(json.dumps(old), encoding="utf-8")
    cfg = Config(target)
    assert cfg.get("kobo_backup") == old["kobo_backup"]
    assert cfg.get("source_sync_consent") is None
    cfg.save()
    reopened = Config(target)
    assert reopened.get("kobo_backup") == old["kobo_backup"]
    assert reopened.get("source_sync_consent") is None
    assert json.loads(target.read_text(encoding="utf-8"))["kobo_backup"] == old["kobo_backup"]


def test_missing_legacy_kobo_settings_have_no_synthetic_consent(tmp_path):
    target = tmp_path / "no-legacy.json"
    target.write_text(json.dumps({
        "providers": {"accounts": [{"id": "synthetic", "provider": "kobo",
                                     "enabled": True, "options": {}}]},
    }), encoding="utf-8")
    cfg = Config(target)
    assert cfg.get("kobo_backup") is None
    assert cfg.get("source_sync_consent") is None
    assert cfg.provider_option("kobo", "sync_consent", credential_id="synthetic") is None
    cfg.save()
    assert "kobo_backup" not in json.loads(target.read_text(encoding="utf-8"))
