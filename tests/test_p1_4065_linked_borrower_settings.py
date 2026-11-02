"""#4065: per-linked-reader settings with exact provider/identity/credential scope.

No network, authentication, GTK runtime or supplier IDs in fixtures.
"""
from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace as NS

import pytest

from cifereca.config import Config
from cifereca.models import Account
from cifereca.providers.base import (
    BorrowerScopedOptionField, CredentialField,
    ProviderCapabilities, ProviderDescriptor,
)
from cifereca.gui.borrower_scoped_options import (
    decimal_limit, linked_borrower_choices, validate_saved_borrower_limits,
)
from cifereca.gui.settings_account_editor import build_provider_account_editor
from cifereca.gui.settings_draft import build_settings_draft, SettingsValidationError
from tests.test_core_ui_1127_provider_account_editor import Widget


SPEC = BorrowerScopedOptionField(
    key="manual_loan_limits_linked",
    label="Plafond personnel du compte lié",
    branches=(("digital", "Numérique"), ("physical", "Physique")),
    default="4", min_value=0, max_value=99,
    help_text="Indicatif, jamais une autorisation.",
)
DESC = ProviderDescriptor(
    id="fixture", display_name="Fournisseur factice", description="Fixture",
    state="ready", capabilities=ProviderCapabilities(loans=True),
    credential_fields=(CredentialField("username", "Utilisateur"),),
    borrower_scoped_options=(SPEC,),
)


def account(borrower, *, identity="alice", credential="conn-a", provider="fixture",
            name="Lecteur associé"):
    return Account(unique_id=borrower, is_primary=False, identity_id=identity,
                   credential_id=credential, provider_id=provider,
                   display_name=name)


def test_known_and_offline_linked_borrowers_only_in_exact_scope():
    saved = {"old-offline": {"digital": "3", "physical": "9"}}
    rows = (account("linked-a"), account("other-id", identity="bob"),
            account("other-credential", credential="conn-b"),
            account("other-provider", provider="other"),
            Account(unique_id="", is_primary=True, identity_id="alice",
                    credential_id="conn-a", provider_id="fixture"))
    choices = linked_borrower_choices(
        saved, SPEC, rows, provider_id="fixture",
        credential_id="conn-a", identity_id="alice",
    )
    assert set(dict(choices)) == {"linked-a", "old-offline"}
    assert "Lecteur associé" in dict(choices)["linked-a"]
    assert "Compte lié" in dict(choices)["linked-a"]
    assert dict(choices)["old-offline"].startswith("Compte lié")
    assert "other-id" not in dict(choices)
    assert "other-credential" not in dict(choices)


@pytest.mark.parametrize("value", ("", "00", "-1", "1.5", " 4", "4 ",
                                   "100", "999", "true", True, None, 4, {}, []))
def test_invalid_values_never_coerce(value):
    assert decimal_limit(value, SPEC) is False


@pytest.mark.parametrize("bad", (
    {"linked": {"digital": True}},
    {"linked": {"digital": "100"}},
    {"linked": {"unknown": "4"}},
    {"": {"digital": "4"}},
    {123: {"digital": "4"}},
    [],
))
def test_malformed_saved_map_is_rejected_without_guess(bad):
    with pytest.raises(ValueError):
        validate_saved_borrower_limits(bad, SPEC)


def editor(data, observed=()):
    gtk = NS(Switch=Widget, Button=lambda label=None: Widget(),
             Align=NS(CENTER="center"))
    adw = NS(PreferencesGroup=Widget, ActionRow=Widget, EntryRow=Widget,
             PasswordEntryRow=Widget)
    config = NS(provider_option=lambda *args: None, get=lambda *args: None)
    ed, button = build_provider_account_editor(
        data=data, provider_id="fixture", account_id="conn-a",
        descriptor=DESC, configured_identities=[{"id": "alice"}],
        accounts_container=Widget(), config=config, Gtk=gtk, Adw=adw,
        observed_borrowers=observed,
    )
    return ed


def data(saved=None):
    return {
        "id": "conn-a", "provider": "fixture", "identity_id": "alice",
        "enabled": True, "credentials": {"username": "synthetic"},
        "options": {
            "opaque": {"preserve": True},
            **({"manual_loan_limits_linked": saved} if saved is not None else {}),
        },
    }


def test_generic_gtk_editor_shows_exact_linked_rows_and_defaults_offline():
    row = editor(data({"old-offline": {"digital": "2", "physical": "5"}}),
                 observed=(account("linked-a"), account("no", identity="bob")))
    values = row["scoped_option_fields"][SPEC.key]
    assert set(values) == {"linked-a", "old-offline"}
    assert values["linked-a"]["digital"].text == "4"
    assert values["linked-a"]["physical"].text == "4"
    assert values["old-offline"]["digital"].text == "2"
    assert values["old-offline"]["physical"].text == "5"
    assert row["invalid_saved_snapshot"] is False
    assert "opaque" in row["original_options"]


def test_corrupt_stored_map_blocks_unrelated_settings_save():
    row = editor(data({"linked-a": {"digital": True}}),
                 observed=(account("linked-a"),))
    assert row["invalid_saved_snapshot"] is True
    assert row["scoped_option_fields"][SPEC.key] == {}


def draft_inputs(tmp_path, *, saved=None, field_values=None, original_identity="alice"):
    existing = data(saved)
    existing["identity_id"] = original_identity
    cfg = Config(tmp_path / "config.json")
    cfg.set("identities", [{"id": "alice", "directory_name": "alice"},
                           {"id": "bob", "directory_name": "bob"}])
    cfg.set("providers.accounts", [existing])
    fields = field_values if field_values is not None else {
        "linked-a": {"digital": "0", "physical": "99"},
    }
    values = {
        "identities": [
            dict(id=x, label=x, directory=x, adept_profile="",
                 languages="fr", prefer_vo=False) for x in ("alice", "bob")
        ],
        "accounts": [dict(
            id="conn-a", provider="fixture", identity="alice",
            enabled=True, label="Fournisseur",
            fields={"username": "synthetic"},
            original_options=deepcopy(existing["options"]),
            original_credentials=deepcopy(existing["credentials"]),
            original_account=deepcopy(existing),
            scoped_option_fields={SPEC.key: fields},
            option_fields={}, invalid_saved_snapshot=False,
        )],
        "provider_downloads": {}, "levels": ("errors", "normal"),
        "diagnostics_level_index": 0,
        "library_directory": str(tmp_path / "books"),
        "download_command": "", "post_download_command": "",
        "download_directory": "", "ebook_directory": "",
        "audiobook_directory": "", "ai": {},
    }
    return cfg, values


def validate(cfg, values):
    return build_settings_draft(
        cfg, values, descriptors={"fixture": DESC}, public_ids=set(),
        current_dirs={"alice": "alice", "bob": "bob"},
    )


def test_settings_roundtrip_keeps_opaque_and_each_branch(tmp_path):
    cfg, values = draft_inputs(
        tmp_path, saved={"old-offline": {"digital": "2", "physical": "5"}},
    )
    before = deepcopy(cfg.data)
    saved = validate(cfg, values).data["providers"]["accounts"][0]
    assert cfg.data == before and not cfg.path.exists()
    assert saved["options"]["opaque"] == {"preserve": True}
    assert saved["options"][SPEC.key] == {
        "old-offline": {"digital": "2", "physical": "5"},
        "linked-a": {"digital": "0", "physical": "99"},
    }
    assert saved["credentials"] == {"username": "synthetic"}
    assert saved["identity_id"] == "alice"
    assert saved["enabled"] is True


@pytest.mark.parametrize("bad", ("", "-1", "1.5", "100", True, 4, "4 "))
def test_settings_rejects_bad_scoped_value_without_writing(tmp_path, bad):
    cfg, values = draft_inputs(tmp_path, field_values={
        "linked-a": {"digital": bad, "physical": "4"},
    })
    original = deepcopy(cfg.data)
    with pytest.raises(SettingsValidationError):
        validate(cfg, values)
    assert cfg.data == original and not cfg.path.exists()


def test_settings_rejects_primary_as_linked_and_identity_transfer(tmp_path):
    cfg, values = draft_inputs(tmp_path, field_values={
        "": {"digital": "4", "physical": "4"},
    })
    with pytest.raises(SettingsValidationError):
        validate(cfg, values)
    cfg, values = draft_inputs(
        tmp_path, saved={"linked-a": {"digital": "1", "physical": "2"}},
        original_identity="bob",
    )
    with pytest.raises(SettingsValidationError, match="Déplacer un compte"):
        validate(cfg, values)


def test_settings_rejects_linked_quota_transfer_to_another_login(tmp_path):
    cfg, values = draft_inputs(
        tmp_path, saved={"linked-a": {"digital": "2", "physical": "4"}},
    )
    values["accounts"][0]["fields"]["username"] = "different-reader"
    with pytest.raises(SettingsValidationError, match="identifiant de connexion"):
        validate(cfg, values)
    assert not cfg.path.exists()
