"""#3731: descriptor/legacy fallbacks never import another account's options.

The Config.provider_option public API deliberately permits first-account
lookup when no credential_id is given for legacy callers. GTK Settings may
never use that unscoped mode while editing or creating another connection.
"""
from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace as NS

import pytest

from cifereca.config import Config
from cifereca.gui.settings_account_editor import build_provider_account_editor
from tests.test_core_ui_1127_provider_account_editor import Widget


def _render(config, data, account_id):
    option = NS(
        key="endpoint", label="Endpoint", secret=False,
        default="https://descriptor.example", legacy_config_key="old_endpoint",
    )
    descriptor = NS(
        display_name="Future provider", credential_fields=(),
        account_options=(option,), account_note=None,
    )
    gtk = NS(Switch=Widget, Button=lambda label=None: Widget(),
             Align=NS(CENTER="center"))
    adw = NS(PreferencesGroup=Widget, ActionRow=Widget,
             EntryRow=Widget, PasswordEntryRow=Widget)
    editor, _ = build_provider_account_editor(
        data=data, provider_id="future-provider", account_id=account_id,
        descriptor=descriptor, configured_identities=[{"id": "default"}],
        accounts_container=Widget(), config=config, Gtk=gtk, Adw=adw,
    )
    return editor["option_fields"]["endpoint"].text


@pytest.mark.parametrize("has_global", [False, True])
def test_editing_second_account_or_creating_new_never_imports_first_account_url(
    tmp_path, has_global,
):
    config = Config(tmp_path / "settings.json")
    first = {
        "id": "first", "provider": "future-provider", "identity_id": "default",
        "enabled": True, "options": {"endpoint": "https://first.example"},
    }
    second = {
        "id": "second", "provider": "future-provider", "identity_id": "default",
        "enabled": False, "options": {},
    }
    config.set("providers.accounts", [deepcopy(first), deepcopy(second)])
    if has_global:
        config.set("old_endpoint", "https://legacy-global.example")
    before = deepcopy(config.data)
    # This unscoped API behavior is intentionally retained for old callers.
    assert config.provider_option("future-provider", "endpoint") == "https://first.example"
    assert _render(config, first, "first") == "https://first.example"
    # A global URL has no credential owner and must not be inherited by
    # *either* row once the provider has two saved accounts.
    assert _render(config, second, "second") == "https://descriptor.example"
    # New sources are never initialized from a retired global value.
    assert _render(config, {}, "third-new") == "https://descriptor.example"
    assert config.data == before
    assert not config.path.exists()


def test_first_account_value_is_never_used_as_another_disabled_account_default(tmp_path):
    config = Config(tmp_path / "settings.json")
    config.set("providers.accounts", [
        {"id": "first", "provider": "future-provider", "enabled": True,
         "options": {"endpoint": "https://owner.example"}},
        {"id": "second", "provider": "future-provider", "enabled": False,
         "options": {"endpoint": ""}},
    ])
    target = config.provider_account("future-provider", "second")
    assert _render(config, target, "second") == ""
    assert _render(config, {}, "new") == "https://descriptor.example"
    assert config.get("source_sync_consent") is None



def test_one_existing_legacy_owner_keeps_historical_global_endpoint(tmp_path):
    config = Config(tmp_path / "settings.json")
    only = {
        "id": "historical", "provider": "future-provider",
        "identity_id": "alice", "enabled": True, "options": {},
    }
    config.set("providers.accounts", [deepcopy(only)])
    config.set("old_endpoint", "https://historical.example")
    before = deepcopy(config.data)
    assert _render(config, only, "historical") == "https://historical.example"
    # An edited/forged row cannot adopt a global option it never owned.
    assert _render(config, only, "another-id") == "https://descriptor.example"
    assert _render(config, {}, "new-id") == "https://descriptor.example"
    assert config.data == before and not config.path.exists()


@pytest.mark.parametrize("variant", ("second_profile", "disabled_second", "case_alias"))
def test_multiaccount_global_url_never_leaks_across_credentials_or_profiles(
    tmp_path, variant,
):
    config = Config(tmp_path / "settings.json")
    first = {
        "id": "first", "provider": "future-provider", "identity_id": "alice",
        "enabled": True, "options": {},
    }
    second = {
        "id": "second", "provider": "future-provider", "identity_id": "bob",
        "enabled": variant != "disabled_second", "options": {},
    }
    if variant == "case_alias":
        second["provider"] = "FUTURE-PROVIDER"
    if variant == "disabled_second":
        second["identity_id"] = "alice"
    config.set("providers.accounts", [first, second])
    config.set("old_endpoint", "https://someone-else.example")
    before = deepcopy(config.data)
    assert _render(config, first, "first") == "https://descriptor.example"
    assert _render(config, second, "second") == "https://descriptor.example"
    assert config.data == before and not config.path.exists()


@pytest.mark.parametrize("accounts", (
    "corrupt", None, [{"provider": "future-provider", "id": "first"}, None],
    [{"provider": "future-provider", "id": "first"}, {"id": "unknown"}],
))
def test_corrupt_or_unattributable_global_endpoint_fails_closed(tmp_path, accounts):
    config = Config(tmp_path / "settings.json")
    config.set("providers.accounts", deepcopy(accounts))
    config.set("old_endpoint", "https://private-unknown-owner.example")
    row = {
        "id": "first", "provider": "future-provider", "identity_id": "default",
        "options": {},
    }
    assert _render(config, row, "first") == "https://descriptor.example"
