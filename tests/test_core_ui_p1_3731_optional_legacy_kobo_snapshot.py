"""#3731: obsolete Kobo editor snapshots cannot mutate the generic Settings draft.

Compatibility adapter tests remain in test_settings_legacy_kobo.py; the active
Settings surface is provider-neutral and preserves old values byte-for-byte.
"""
from copy import deepcopy

import pytest

from tests.test_settings_draft import editor, validate, account, descriptor


LEGACY = ("backup_interval", "vault_profile_index", "backup_directory", "vault_profiles")


def _stored(config):
    return deepcopy(config.data.get("kobo_backup"))


def test_future_provider_neutral_editor_can_omit_every_legacy_kobo_widget(editor):
    config, values = editor
    config.set("kobo_backup", {
        "enabled": True, "allow_unlinked_acquisition": False,
        "directory": "original-location", "interval_minutes": 43,
        "display_profile": "Original", "provider_extension": {"opaque": "keep"},
    })
    before = deepcopy(config.data)
    for key in LEGACY:
        del values[key]
    values["accounts"] = [account("provider.future")]
    draft = validate(editor, descriptors={"provider.future": descriptor("provider.future")})
    assert draft.data.get("kobo_backup") == before.get("kobo_backup")
    assert draft.data["providers"]["accounts"][0]["provider"] == "provider.future"
    assert config.data == before and not config.path.exists()


@pytest.mark.parametrize("missing", LEGACY)
def test_partial_obsolete_widget_snapshot_is_ignored_without_reset(editor, missing):
    config, values = editor
    del values[missing]
    before = deepcopy(config.data)
    draft = validate(editor)
    assert draft.data.get("kobo_backup") == before.get("kobo_backup")
    assert config.data == before and not config.path.exists()


@pytest.mark.parametrize(("key", "invalid"), [
    ("backup_interval", None), ("backup_interval", 30),
    ("backup_directory", {}), ("vault_profile_index", True),
    ("vault_profile_index", "1"), ("vault_profiles", "Android"),
    ("vault_profiles", {"0": "Android"}), ("vault_profiles", [99]),
])
def test_malformed_obsolete_widget_snapshot_cannot_mutate_settings(editor, key, invalid):
    config, values = editor
    values[key] = invalid
    before = deepcopy(config.data)
    draft = validate(editor)
    assert draft.data.get("kobo_backup") == before.get("kobo_backup")
    assert config.data == before and not config.path.exists()


def test_full_legacy_editor_values_are_ignored_without_regranting_consent(editor):
    config, values = editor
    config.set("kobo_backup", {
        "enabled": True, "allow_unlinked_acquisition": True,
        "directory": "old", "interval_minutes": 300,
        "display_profile": "Old", "private_extension": 15,
    })
    before = deepcopy(config.data)
    values["backup_directory"] = "new"
    values["backup_interval"] = "12"
    values["vault_profile_index"] = 1
    values["backup_enabled"] = False
    values["allow_unlinked_acquisition"] = False
    draft = validate(editor)
    assert draft.data.get("kobo_backup") == before.get("kobo_backup")
    assert config.data == before and not config.path.exists()


def test_generic_draft_source_contains_no_legacy_adapter_call():
    from pathlib import Path
    source = (Path(__file__).resolve().parents[1] /
              "src/cifereca/gui/settings_draft.py").read_text(encoding="utf-8")
    assert "apply_legacy_kobo_settings" not in source
    assert "settings_legacy_kobo import" not in source
