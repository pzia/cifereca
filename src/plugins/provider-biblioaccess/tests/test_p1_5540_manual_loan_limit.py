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
    def get_active(self): return self.active
    def set_valign(self, _align): pass
    def add(self, item): self.children.append(item)
    def append(self, item): self.children.append(item)
    def add_suffix(self, item): self.suffixes.append(item)
    def add_css_class(self, _name): pass


class Combo(Widget):
    def set_model(self, labels): self.model = tuple(labels)
    def set_selected(self, index): self.selected = index
    def get_selected(self): return self.selected


GTK = NS(Switch=Widget, Button=Widget, Align=NS(CENTER="center"),
         StringList=NS(new=lambda labels: tuple(labels)))
ADW = NS(PreferencesGroup=Widget, ActionRow=Widget,
         EntryRow=Widget, PasswordEntryRow=Widget, ComboRow=Combo)


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
    assert options["authenticated_sync"].value_type == "choice"
    assert tuple(value for value, _label in options["authenticated_sync"].choices) == ("off", "on")
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
    assert editor_a["option_fields"]["authenticated_sync"].row.model == (
        "Désactivée — sans connexion automatique",
        "Activée — CAS, prêts et acquisitions permises",
    )
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


def test_sync_choice_roundtrip_preserves_each_credentials_consent_and_old_options(tmp_path):
    # Existing persisted "on"/"off" strings must not be migrated to labels or
    # bools. The CAS connection code continues to check exact "on" only.
    rows = [saved("cred-a", "alice", sync="off"),
            saved("cred-b", "bob", sync="on", ceiling="7")]
    before = deepcopy(rows)
    a, _ = render(rows[0])
    b, _ = render(rows[1])
    option_a = a["option_fields"]["authenticated_sync"]
    option_b = b["option_fields"]["authenticated_sync"]
    assert option_a.get_text() == "off"
    assert option_a.row.get_selected() == 0
    assert option_b.get_text() == "on"
    assert option_b.row.get_selected() == 1
    assert a["probe_rendered_baseline"]["option_fields"]["authenticated_sync"] == "off"
    assert b["probe_rendered_baseline"]["option_fields"]["authenticated_sync"] == "on"
    option_a.row.set_selected(1)
    option_b.row.set_selected(0)
    assert option_a.get_text() == "on"
    assert option_b.get_text() == "off"
    assert rows == before  # GTK rendering and editing never write Config

    config = Config(tmp_path / "config.json")
    config.set("identities", [
        {"id": "alice", "directory_name": "alice"},
        {"id": "bob", "directory_name": "bob"},
    ])
    config.set("providers.accounts", [])
    form = values(tmp_path, rows)
    form["accounts"][0]["option_fields"]["authenticated_sync"] = option_a.get_text()
    form["accounts"][1]["option_fields"]["authenticated_sync"] = option_b.get_text()
    saved_draft = draft(config, form)
    per_account = {
        row["id"]: row for row in saved_draft.data["providers"]["accounts"]
    }
    assert per_account["cred-a"]["options"]["authenticated_sync"] == "on"
    assert per_account["cred-b"]["options"]["authenticated_sync"] == "off"
    assert per_account["cred-a"]["options"]["retired_option"] == {"opaque": True}
    assert per_account["cred-b"]["options"]["manual_loan_limit"] == "7"
    assert per_account["cred-a"]["identity_id"] == "alice"
    assert per_account["cred-b"]["identity_id"] == "bob"


@pytest.mark.parametrize("invalid", [
    "ON", "true", "1", " Activée", "", "off ", None, True, 1, {"on": True},
])
def test_stale_or_invalid_sync_optin_never_silently_selects_first_or_writes(
    tmp_path, invalid,
):
    # The user must resolve stale options deliberately rather than trigger
    # an unrelated Save that changes a borrower's SSO consent.
    data = saved("cred-a", "alice", sync=invalid)
    editor, _ = render(data)
    option = editor["option_fields"]["authenticated_sync"]
    assert editor["invalid_saved_snapshot"] is True
    assert option.row.get_selected() == 0xFFFFFFFF
    assert option.get_text() == ""

    config = Config(tmp_path / "config.json")
    config.set("identities", [
        {"id": "alice", "directory_name": "alice"},
        {"id": "bob", "directory_name": "bob"},
    ])
    config.set("providers.accounts", [])
    form = values(tmp_path, [data])
    before = deepcopy(config.data)
    with pytest.raises(SettingsValidationError):
        draft(config, form)
    assert config.data == before and not config.path.exists()


def test_missing_sync_option_keeps_off_default_without_activating_sso(tmp_path):
    data = saved("cred-a", "alice")
    data["options"].pop("authenticated_sync")
    editor, _ = render(data)
    assert editor["invalid_saved_snapshot"] is False
    assert editor["option_fields"]["authenticated_sync"].get_text() == "off"
    assert editor["probe_rendered_baseline"]["option_fields"]["authenticated_sync"] == "off"
    config = Config(tmp_path / "config.json")
    config.set("identities", [
        {"id": "alice", "directory_name": "alice"},
        {"id": "bob", "directory_name": "bob"},
    ])
    config.set("providers.accounts", [])
    form = values(tmp_path, [data])
    # This historical helper indexes the legacy value. A real GTK commit
    # collects the displayed default from the ComboRow instead.
    form["accounts"][0]["option_fields"]["authenticated_sync"] = (
        editor["option_fields"]["authenticated_sync"].get_text()
    )
    result = draft(config, form)
    assert result.data["providers"]["accounts"][0]["options"]["authenticated_sync"] == "off"
