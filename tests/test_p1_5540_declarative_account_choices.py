"""#5540: generic declared settings choices; fake GTK and real draft validation.

No provider-specific branch, login, scheduler or supplier I/O.
"""
from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace as NS

import pytest

from cifereca.config import Config
from cifereca.gui.settings_account_editor import build_provider_account_editor
from cifereca.gui.settings_draft import build_settings_draft, SettingsValidationError
from cifereca.providers.base import validated_account_option_choices
from cifereca.plugin_api import (
    AccountOptionField, CredentialField, ProviderCapabilities,
    ProviderDescriptor,
)
from tests.test_core_ui_1127_provider_account_editor import Widget


PROVIDER = "synthetic-settings-choices"
CHOICES = (("off", "Aucune synchronisation"), ("on", "Activer la synchronisation"))


class ComboRow(Widget):
    def __init__(self):
        super().__init__()
        self.selected = 0xFFFFFFFF
        self.model = ()
    def set_model(self, model):
        self.model = tuple(model)
    def set_selected(self, index):
        self.selected = index
    def get_selected(self):
        return self.selected


class StringList:
    @staticmethod
    def new(labels):
        return tuple(labels)


GTK = NS(Switch=Widget, Button=lambda **kw: Widget(),
         Align=NS(CENTER="center"), StringList=StringList)
ADW = NS(PreferencesGroup=Widget, ActionRow=Widget, EntryRow=Widget,
         PasswordEntryRow=Widget, ComboRow=ComboRow)


def descriptor(spec=None):
    return ProviderDescriptor(
        id=PROVIDER, display_name="Fournisseur synthétique",
        description="Fixture sans réseau", state="ready",
        capabilities=ProviderCapabilities(authentication=True),
        credential_fields=(CredentialField("username", "Compte"),),
        account_options=(spec or AccountOptionField(
            "authenticated_sync", "Synchronisation", value_type="choice",
            choices=CHOICES, default="off", help_text="Préférence locale",
        ),),
    )


def stored(credential, owner, option=None):
    values = {"retired_opaque": {"old": True}}
    if option is not None:
        values["authenticated_sync"] = option
    return {
        "id": credential, "provider": PROVIDER, "identity_id": owner,
        "label": "Compte fictif", "enabled": True,
        "credentials": {"username": "SYNTHETIC"},
        "options": values,
    }


def render(row, spec=None):
    outer = Widget()
    editor, delete = build_provider_account_editor(
        data=row, provider_id=PROVIDER, account_id=row["id"],
        descriptor=descriptor(spec), configured_identities=[
            {"id": "alice"}, {"id": "bob"}],
        accounts_container=outer, config=NS(
            provider_option=lambda *a, **kw: None,
            get=lambda *a, **kw: None),
        Gtk=GTK, Adw=ADW,
    )
    return editor, outer.children[0]


def form_values(tmp_path, rows, changes=None):
    changes = changes or {}
    return {
        "identities": [
            {"id": who, "label": who, "directory": who, "adept_profile": "",
             "languages": "fr", "prefer_vo": False}
            for who in ("alice", "bob")
        ],
        "accounts": [
            {
                "id": row["id"], "provider": PROVIDER, "identity": row["identity_id"],
                "label": row["label"], "enabled": row["enabled"],
                "fields": dict(row["credentials"]),
                "original_credentials": dict(row["credentials"]),
                "original_options": deepcopy(row["options"]),
                "original_account": deepcopy(row),
                "option_fields": {"authenticated_sync":
                    changes.get(row["id"], row["options"].get("authenticated_sync", "off"))},
            }
            for row in rows
        ],
        "provider_downloads": {}, "levels": ["normal"],
        "diagnostics_level_index": 0,
        "vault_profiles": ["Desktop"], "vault_profile_index": 0,
        "diagnostics_enabled": False, "experimental_actions": False,
        "backup_enabled": False, "allow_unlinked_acquisition": False,
        "backup_interval": "60", "library_directory": str(tmp_path / "books"),
        "backup_directory": "", "download_command": "",
        "post_download_command": "", "download_directory": "",
        "ebook_directory": "", "audiobook_directory": "", "ai": {},
    }


def draft(tmp_path, rows, changes=None, spec=None):
    config = Config(tmp_path / "config.json")
    config.set("identities", [
        {"id": who, "directory_name": who} for who in ("alice", "bob")
    ])
    config.set("providers.accounts", [])
    form = form_values(tmp_path, rows, changes)
    before = deepcopy(config.data)
    result = build_settings_draft(
        config, form, descriptors={PROVIDER: descriptor(spec)},
        public_ids=set(), current_dirs={"alice": "alice", "bob": "bob"},
    )
    assert config.data == before
    return result


def test_choice_combo_renders_labels_but_persists_only_exact_tokens():
    one, group = render(stored("cred-one", "alice"))
    two, _ = render(stored("cred-two", "bob", "on"))
    first = one["option_fields"]["authenticated_sync"]
    second = two["option_fields"]["authenticated_sync"]
    assert isinstance(first.row, ComboRow)
    assert first.row.model == tuple(label for _token, label in CHOICES)
    assert first.get_text() == "off"
    assert second.get_text() == "on"
    assert one["probe_rendered_baseline"]["option_fields"] == {
        "authenticated_sync": "off"}
    assert one["invalid_saved_snapshot"] is False
    assert any(row.title == "Synchronisation" for row in group.children)
    first.row.set_selected(1)
    assert first.get_text() == "on"
    assert second.get_text() == "on"
    second.row.set_selected(0)
    assert second.get_text() == "off"


def test_two_connections_and_old_opaque_options_remain_isolated(tmp_path):
    rows = [stored("cred-one", "alice"),
            stored("cred-two", "bob", "off")]
    saved = draft(tmp_path, rows, {"cred-two": "on"})
    options = {x["id"]: x["options"] for x in saved.data["providers"]["accounts"]}
    assert options["cred-one"]["authenticated_sync"] == "off"
    assert options["cred-two"]["authenticated_sync"] == "on"
    assert options["cred-one"]["retired_opaque"] == {"old": True}
    assert options["cred-two"]["retired_opaque"] == {"old": True}
    scoped = {x["id"]: x["identity_id"] for x in saved.data["providers"]["accounts"]}
    assert scoped == {"cred-one": "alice", "cred-two": "bob"}


@pytest.mark.parametrize("invalid", [
    "true", "ON", "Aucune synchronisation", " disabled ", "", 1,
    True, None, (), {"on": True},
])
def test_undeclared_stale_and_untyped_values_refuse_any_draft_write(tmp_path, invalid):
    config = Config(tmp_path / "config.json")
    config.set("identities", [{"id": "alice", "directory_name": "alice"},
                              {"id": "bob", "directory_name": "bob"}])
    config.set("providers.accounts", [])
    values = form_values(tmp_path, [stored("cred-one", "alice")],
                         {"cred-one": invalid})
    before = deepcopy(config.data)
    with pytest.raises(SettingsValidationError):
        build_settings_draft(
            config, values, descriptors={PROVIDER: descriptor()},
            public_ids=set(), current_dirs={"alice": "alice", "bob": "bob"},
        )
    assert config.data == before and not config.path.exists()


@pytest.mark.parametrize("choices,default,secret", [
    ((("off", "Non"), ("off", "Oui")), "off", False),
    ((("off", "Non"), ("on", "Oui")), "missing", False),
    ((("off", ""),), "off", False),
    ((("off\nsecret", "Non"),), "off\nsecret", False),
    ((("off", "Non"),), "off", True),
    ([], "off", False),
    ((("off", "Non"), ("on", object())), "off", False),
])
def test_invalid_descriptors_cannot_infer_a_choice_or_crash(
    tmp_path, choices, default, secret,
):
    spec = AccountOptionField(
        "authenticated_sync", "Synchronisation", value_type="choice",
        choices=choices, default=default, secret=secret,
    )
    assert validated_account_option_choices(spec) is None
    row = stored("cred-one", "alice")
    editor, _group = render(row, spec)
    assert editor["invalid_saved_snapshot"] is True
    assert editor["option_fields"]["authenticated_sync"].get_text() == ""
    with pytest.raises(SettingsValidationError):
        draft(tmp_path, [row], spec=spec)


def test_foreign_saved_choice_never_autoselects_first_allowed_value(tmp_path):
    row = stored("cred-one", "alice", "retired-on")
    editor, _group = render(row)
    choice = editor["option_fields"]["authenticated_sync"]
    assert choice.get_text() == "" and choice.row.get_selected() == 0xFFFFFFFF
    assert editor["invalid_saved_snapshot"] is True
    with pytest.raises(SettingsValidationError):
        draft(tmp_path, [row], {"cred-one": "retired-on"})


def test_valid_choice_schema_is_bounded_and_backwards_compatible():
    assert validated_account_option_choices(descriptor().account_options[0]) == CHOICES
    ordinary = AccountOptionField("endpoint", "Portail", default="https://test.invalid")
    assert ordinary.value_type == "text"
    assert ordinary.choices == ()
