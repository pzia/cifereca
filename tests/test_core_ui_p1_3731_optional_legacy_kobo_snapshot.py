"""#3731: retiring the Kobo-specific Settings editor must not reset its legacy state.

All tests run on a draft and synthetic Config; no provider I/O, disk write,
automatic-acquisition grant, or user account is touched.
"""
from copy import deepcopy

import pytest

from cifereca.gui.settings_draft import SettingsValidationError
from tests.test_settings_draft import editor, validate, account, descriptor


LEGACY = ("backup_interval", "vault_profile_index", "backup_directory", "vault_profiles")


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
    assert draft.data["kobo_backup"] == before["kobo_backup"]
    assert draft.data["providers"]["accounts"][0]["provider"] == "provider.future"
    assert config.data == before and not config.path.exists()


@pytest.mark.parametrize("missing", LEGACY)
def test_partial_kobo_widget_snapshot_is_refused_without_reset(editor, missing):
    config, values = editor
    del values[missing]
    before = deepcopy(config.data)
    with pytest.raises(SettingsValidationError, match="Configuration Kobo incomplète"):
        validate(editor)
    assert config.data == before and not config.path.exists()


@pytest.mark.parametrize(("key", "invalid", "message"), [
    ("backup_interval", None, "Configuration Kobo invalide"),
    ("backup_interval", 30, "Configuration Kobo invalide"),
    ("backup_directory", {}, "Configuration Kobo invalide"),
    ("vault_profile_index", True, "Profil de sauvegarde historique invalide"),
    ("vault_profile_index", "1", "Profil de sauvegarde historique invalide"),
    ("vault_profiles", "Android", "Profil de sauvegarde historique invalide"),
    ("vault_profiles", {"0": "Android"}, "Profil de sauvegarde historique invalide"),
    ("vault_profiles", [99], "Profil de sauvegarde historique invalide"),
])
def test_kobo_legacy_widget_snapshot_rejects_malformed_fields(
    editor, key, invalid, message,
):
    config, values = editor
    values[key] = invalid
    before = deepcopy(config.data)
    with pytest.raises(SettingsValidationError, match=message):
        validate(editor)
    assert config.data == before and not config.path.exists()


def test_full_legacy_editor_still_updates_only_non_permission_kobo_keys(editor):
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
    draft = validate(editor)
    changed = draft.data["kobo_backup"]
    assert changed["directory"] == "new"
    assert changed["interval_minutes"] == 15
    assert changed["display_profile"] == "Desktop"
    assert changed["enabled"] is True
    assert changed["allow_unlinked_acquisition"] is True
    assert changed["private_extension"] == 15
    assert config.data == before and not config.path.exists()
