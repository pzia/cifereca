"""P1 #3731: malformed persisted enabled flags cannot be silently rewritten.

GTK remains safe to display an untyped source as OFF. Saving an unrelated
setting must not transform that *stored* malformed value into a new boolean,
while a historical *missing* enabled field remains compatible.
"""
from __future__ import annotations

from copy import deepcopy

import pytest

from cifereca.gui.settings_draft import SettingsValidationError
from tests.test_core_ui_1127_provider_account_editor import _render
from tests.test_settings_draft import account, descriptor, editor, validate


@pytest.mark.parametrize("bad", ["false", "true", None, 0, 1, [], {}])
def test_existing_corrupt_enabled_is_visually_off_and_flags_invalid(bad):
    original = {
        "id": "source", "provider": "future-provider",
        "enabled": bad, "identity_id": "alice", "credentials": {},
    }
    shown, _, _, _ = _render("future-provider", data=original)
    assert shown["enabled"].active is False
    assert shown["invalid_saved_snapshot"] is True
    assert shown["original_account"]["enabled"] == bad
    assert original["enabled"] == bad


@pytest.mark.parametrize("bad", ["false", "true", None, 0, 1, [], {}])
def test_stale_draft_refuses_to_normalize_malformed_persisted_activation(editor, bad):
    config, values = editor
    stored = {
        "id": "source", "provider": "future-provider",
        "identity_id": "alice", "enabled": bad,
        "credentials": {"username": "synthetic-user"},
        "options": {"opaque": {"keep": "yes"}},
    }
    config.set("providers.accounts", [deepcopy(stored)])
    row = account("future-provider")
    row.update(
        enabled=False, original_account=deepcopy(stored),
        original_credentials=deepcopy(stored["credentials"]),
        original_options=deepcopy(stored["options"]),
    )
    values["accounts"] = [row]
    values["identities"][0]["label"] = "Unrelated UI change"
    before = deepcopy(config.data)

    with pytest.raises(SettingsValidationError, match="Activation historique invalide"):
        validate(editor, descriptors={"future-provider": descriptor("future-provider")})
    assert config.data == before
    assert config.provider_accounts() == [stored]
    assert not config.path.exists()


@pytest.mark.parametrize("enabled", [True, False])
def test_boolean_persisted_flag_remains_editable_without_implicit_change(editor, enabled):
    config, values = editor
    original = {
        "id": "source", "provider": "future-provider",
        "identity_id": "alice", "enabled": enabled,
        "credentials": {"username": "synthetic-user"}, "options": {},
    }
    config.set("providers.accounts", [deepcopy(original)])
    row = account("future-provider")
    row.update(
        enabled=enabled, original_account=deepcopy(original),
        original_credentials=deepcopy(original["credentials"]),
    )
    values["accounts"] = [row]
    result = validate(editor, descriptors={"future-provider": descriptor("future-provider")})
    saved, = result.data["providers"]["accounts"]
    assert saved["enabled"] is enabled
    assert config.provider_accounts() == [original]
    assert not config.path.exists()


def test_missing_historical_enabled_is_compatible_and_new_account_stays_off(editor):
    config, values = editor
    stored = {
        "id": "source", "provider": "future-provider",
        "identity_id": "alice",
        "credentials": {"username": "synthetic-user"}, "options": {},
    }
    config.set("providers.accounts", [deepcopy(stored)])
    row = account("future-provider")
    row.update(
        original_account=deepcopy(stored),
        original_credentials=deepcopy(stored["credentials"]),
    )
    values["accounts"] = [row]
    saved, = validate(
        editor, descriptors={"future-provider": descriptor("future-provider")},
    ).data["providers"]["accounts"]
    assert saved["enabled"] is True
    assert config.provider_accounts() == [stored]
    assert not config.path.exists()

    new, _, _, _ = _render("future-provider", data={})
    assert new["enabled"].active is False
    assert new["invalid_saved_snapshot"] is False
