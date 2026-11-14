"""P1 #5540 — La Rochelle personal loan ceiling is a generic Settings option.

All data are synthetic. No SSO, GET, POST, catalogue or borrowing operation.
The user-entered number never establishes provider quota or permissions.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.config import Config
from cifereca.gui.settings_account_editor import build_provider_account_editor
from cifereca.gui.settings_draft import build_settings_draft, SettingsValidationError
from cifereca_biblioaccess import DESCRIPTOR


PROVIDER = "biblioaccess.la-rochelle"


class Widget:
    def __init__(self, label=None):
        self.title = self.text = self.subtitle = None
        self.children = []
        self.suffixes = []
        self.active = None
    def set_title(self, text): self.title = text
    def set_subtitle(self, text): self.subtitle = text
    def set_text(self, text): self.text = text
    def get_text(self): return self.text
    def set_active(self, active): self.active = active
    def set_valign(self, _align): pass
    def add(self, item): self.children.append(item)
    def append(self, item): self.children.append(item)
    def add_suffix(self, item): self.suffixes.append(item)
    def add_css_class(self, _name): pass


GTK = NS(Switch=Widget, Button=Widget, Align=NS(CENTER="center"))
ADW = NS(PreferencesGroup=Widget, ActionRow=Widget,
         EntryRow=Widget, PasswordEntryRow=Widget)


def saved(credential, identity, *, ceiling=None, sync="off"):
    options = {"authenticated_sync": sync, "retired_option": {"opaque": True}}
    if ceiling is not None:
        options["manual_loan_limit"] = ceiling
    return {
        "id": credential, "provider": PROVIDER, "identity_id": identity,
        "label": "SYNTHETIC", "enabled": True,
        "credentials": {"username": "SYNTHETIC", "password": "PRIVATE"},
        "options": options,
    }


def render(data):
    root = Widget()
    editor, button = build_provider_account_editor(
        data=data, provider_id=PROVIDER, account_id=data["id"],
        descriptor=DESCRIPTOR, configured_identities=[
            {"id": "alice"}, {"id": "bob"}],
        accounts_container=root, config=NS(
            provider_option=lambda *a, **kw: None,
            get=lambda *a, **kw: None),
        Gtk=GTK, Adw=ADW,
    )
    return editor, root


def test_descriptor_declares_bounded_advisory_not_a_quota_or_sso_grant():
    options = {field.key: field for field in DESCRIPTOR.account_options}
    assert set(options) == {"authenticated_sync", "manual_loan_limit"}
    field = options["manual_loan_limit"]
    assert (field.default, field.value_type, field.min_value, field.max_value) == (
        "4", "unsigned_integer", 0, 99)
    assert "Plafond renseigné par vous" in field.help_text
    assert "ne correspond pas à un quota attesté" in field.help_text
    assert DESCRIPTOR.background_authentication_option_key == "authenticated_sync"
    assert DESCRIPTOR.interactive_authentication is True
    assert options["authenticated_sync"].default == "off"
    assert DESCRIPTOR.capabilities.borrow is False


def test_generic_editor_defaults_and_isolates_two_credentials_without_io():
    first = saved("cred-a", "alice")
    second = saved("cred-b", "bob", ceiling="9")
    before = (deepcopy(first), deepcopy(second))
    editor_a, root_a = render(first)
    editor_b, root_b = render(second)
    assert editor_a["option_fields"]["manual_loan_limit"].text == "4"
    assert editor_b["option_fields"]["manual_loan_limit"].text == "9"
    assert editor_a["option_fields"]["authenticated_sync"].text == "off"
    assert editor_b["option_fields"]["authenticated_sync"].text == "off"
    assert editor_a["identity"].text == "alice"
    assert editor_b["identity"].text == "bob"
    for root in (root_a, root_b):
        titles = [row.title for row in root.children[0].children]
        assert "Maximum d’emprunts (valeur personnelle)" in titles
        assert "Vérifier la connexion au compte" in titles
        assert any("Plafond renseigné par vous" in (row.subtitle or "")
                   for row in root.children[0].children)
    assert (first, second) == before


def values(tmp_path, rows):
    return {
        "identities": [
            dict(id=identity, label=identity, directory=identity,
                 adept_profile="", languages="fr", prefer_vo=False)
            for identity in ("alice", "bob")
        ],
        "accounts": [
            {
                "id": item["id"], "provider": PROVIDER,
                "identity": item["identity_id"], "label": item["label"],
                "enabled": item["enabled"],
                "fields": dict(item["credentials"]),
                "original_credentials": dict(item["credentials"]),
                "original_options": deepcopy(item["options"]),
                "original_account": deepcopy(item),
                "option_fields": {
                    "authenticated_sync": item["options"]["authenticated_sync"],
                    "manual_loan_limit": item["options"].get("manual_loan_limit", "4"),
                },
            }
            for item in rows
        ],
        "provider_downloads": {},
        "levels": ["normal"], "diagnostics_level_index": 0,
        "vault_profiles": ["Desktop"], "vault_profile_index": 0,
        "diagnostics_enabled": False, "experimental_actions": False,
        "backup_enabled": False, "allow_unlinked_acquisition": False,
        "backup_interval": "60", "library_directory": str(tmp_path / "books"),
        "backup_directory": "", "download_command": "",
        "post_download_command": "", "download_directory": "",
        "ebook_directory": "", "audiobook_directory": "", "ai": {},
    }


def draft(config, form):
    return build_settings_draft(
        config, form, descriptors={PROVIDER: DESCRIPTOR},
        public_ids=set(), current_dirs={"alice": "alice", "bob": "bob"},
    )


def test_persistence_roundtrip_and_legacy_missing_default_are_per_connection(tmp_path):
    config = Config(tmp_path / "config.json")
    config.set("identities", [
        {"id": "alice", "directory_name": "alice"},
        {"id": "bob", "directory_name": "bob"},
    ])
    config.set("providers.accounts", [])
    rows = [saved("cred-a", "alice"), saved("cred-b", "bob", ceiling="7")]
    form = values(tmp_path, rows)
    original = deepcopy(config.data)
    parsed = draft(config, form)
    assert config.data == original
    accounts = parsed.data["providers"]["accounts"]
    assert len(accounts) == 2
    by_id = {account["id"]: account for account in accounts}
    assert by_id["cred-a"]["options"]["manual_loan_limit"] == "4"
    assert by_id["cred-b"]["options"]["manual_loan_limit"] == "7"
    assert by_id["cred-a"]["options"]["authenticated_sync"] == "off"
    assert by_id["cred-b"]["options"]["retired_option"] == {"opaque": True}
    assert by_id["cred-a"]["identity_id"] == "alice"
    assert by_id["cred-b"]["identity_id"] == "bob"


@pytest.mark.parametrize("bad", [
    "", "-1", "+4", "04", "100", "999", "4.0", " 4", "4 ", "true",
    None, True, {"value": 4},
])
def test_invalid_manual_preference_never_writes_or_changes_sso(tmp_path, bad):
    config = Config(tmp_path / "config.json")
    config.set("identities", [{"id": "alice", "directory_name": "alice"},
                              {"id": "bob", "directory_name": "bob"}])
    config.set("providers.accounts", [])
    form = values(tmp_path, [saved("cred-a", "alice")])
    form["accounts"][0]["option_fields"]["manual_loan_limit"] = bad
    before = deepcopy(config.data)
    with pytest.raises(SettingsValidationError):
        draft(config, form)
    assert config.data == before and not config.path.exists()
    assert form["accounts"][0]["option_fields"]["authenticated_sync"] == "off"


def test_legacy_untouched_zero_and_99_are_valid_without_claiming_live_quota(tmp_path):
    config = Config(tmp_path / "config.json")
    config.set("identities", [{"id": "alice", "directory_name": "alice"},
                              {"id": "bob", "directory_name": "bob"}])
    config.set("providers.accounts", [])
    for value in ("0", "99"):
        result = draft(config, values(tmp_path, [saved(
            "cred-a", "alice", ceiling=value,
        )]))
        row, = result.data["providers"]["accounts"]
        assert row["options"]["manual_loan_limit"] == value
        assert row["options"]["authenticated_sync"] == "off"
