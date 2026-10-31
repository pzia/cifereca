"""#3731: legacy provider preferences stay outside generic Settings policies."""
from copy import deepcopy

import pytest

from cifereca.gui.settings_legacy_kobo import apply_legacy_kobo_settings
from cifereca.gui.settings_draft import SettingsValidationError


class Draft:
    def __init__(self, data):
        self.data = deepcopy(data)

    def set(self, dotted_key, value):
        parent = self.data
        keys = dotted_key.split(".")
        for key in keys[:-1]:
            parent = parent.setdefault(key, {})
        parent[keys[-1]] = value


def apply(config, values):
    draft = Draft(config)
    apply_legacy_kobo_settings(draft, values, validation_error=SettingsValidationError)
    return draft.data


def test_legacy_kobo_adapter_preserves_all_historical_consent_and_format_values():
    config = {"kobo_backup": {
        "enabled": True, "allow_unlinked_acquisition": True, "format": "kepub",
        "directory": "before", "interval_minutes": 30, "display_profile": "Android",
    }}
    values = {
        "backup_directory": "after", "backup_interval": "90",
        "vault_profile_index": 1, "vault_profiles": ["Android", "Desktop"],
        "backup_enabled": False, "allow_unlinked_acquisition": False,
    }
    result = apply(config, values)
    assert result["kobo_backup"] == {
        "enabled": True, "allow_unlinked_acquisition": True, "format": "kepub",
        "directory": "after", "interval_minutes": 90, "display_profile": "Desktop",
    }
    assert config["kobo_backup"]["directory"] == "before"


def test_adapter_does_not_create_missing_global_consent_keys():
    result = apply({}, {
        "backup_directory": "", "backup_interval": "5",
        "vault_profile_index": 99, "vault_profiles": ["Android", "Desktop"],
    })["kobo_backup"]
    assert result == {
        "directory": "", "interval_minutes": 15, "display_profile": "Android",
    }


@pytest.mark.parametrize("invalid", ["abc", "3.5", ""])
def test_invalid_legacy_interval_fails_as_settings_validation_error(invalid):
    if invalid == "":
        # Blank preserves historical 60-minute default.
        assert apply({}, {
            "backup_directory": "", "backup_interval": invalid,
            "vault_profile_index": 0, "vault_profiles": ["Android"],
        })["kobo_backup"]["interval_minutes"] == 60
        return
    with pytest.raises(SettingsValidationError, match="Configuration Kobo invalide"):
        apply({}, {
            "backup_directory": "", "backup_interval": invalid,
            "vault_profile_index": 0, "vault_profiles": ["Android"],
        })


@pytest.mark.parametrize("invalid", [None, False, "1", [], {}])
def test_untyped_profile_index_is_never_coerced(invalid):
    with pytest.raises(SettingsValidationError, match="Profil de sauvegarde historique invalide"):
        apply({}, {
            "backup_directory": "", "backup_interval": "60",
            "vault_profile_index": invalid, "vault_profiles": ["Android", "Desktop"],
        })
