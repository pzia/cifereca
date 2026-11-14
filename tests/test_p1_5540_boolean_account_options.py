"""P1 #5540: declarative off-by-default switches for provider account opt-ins.

Synthetic credentials and fake GTK; the real Settings draft is used.
No provider connect, permission change, HTTP, SQLite or disk publication.
"""
from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace as NS

import pytest

from cifereca.config import Config
from cifereca.gui.settings_account_editor import build_provider_account_editor
from cifereca.gui.settings_draft import build_settings_draft, SettingsValidationError
from cifereca.plugin_api import AccountOptionField
from cifereca.providers.base import valid_account_option_boolean
from tests.test_p1_5540_declarative_account_choices import (
    ADW, GTK, Widget, descriptor, stored, form_values,
)


class Toggle(Widget):
    def __init__(self):
        super().__init__()
        self.active = False

    def get_active(self):
        return self.active


GTK_WITH_TOGGLE = NS(
    Switch=Toggle, Button=GTK.Button, Align=GTK.Align,
    StringList=GTK.StringList,
)


def field(**kw):
    return AccountOptionField(
        "authenticated_sync", "Synchroniser ce compte", value_type="boolean",
        default=kw.pop("default", "off"), **kw,
    )


def render(row, *, spec=None):
    container = Widget()
    editor, _button = build_provider_account_editor(
        data=row, provider_id=descriptor().id, account_id=row["id"],
        descriptor=descriptor(spec or field()),
        configured_identities=[{"id": "alice"}, {"id": "bob"}],
        accounts_container=container,
        config=NS(provider_option=lambda *a, **kw: None,
                  get=lambda *a, **kw: None),
        Gtk=GTK_WITH_TOGGLE, Adw=ADW,
    )
    return editor, container.children[0]


def make_draft(tmp_path, rows, *, changes=None, spec=None):
    config = Config(tmp_path / "settings.json")
    config.set("identities", [
        {"id": "alice", "directory_name": "alice"},
        {"id": "bob", "directory_name": "bob"},
    ])
    config.set("providers.accounts", [])
    form = form_values(tmp_path, rows, changes=changes)
    baseline = deepcopy(config.data)
    result = build_settings_draft(
        config, form, descriptors={descriptor().id: descriptor(spec or field())},
        public_ids=set(), current_dirs={"alice": "alice", "bob": "bob"},
    )
    assert config.data == baseline
    assert not config.path.exists()
    return result


def test_boolean_contract_requires_nonsecret_explicit_off_default():
    assert valid_account_option_boolean(field()) is True
    assert valid_account_option_boolean(field(default="on")) is False
    assert valid_account_option_boolean(field(secret=True)) is False
    assert valid_account_option_boolean(field(choices=(("on", "Oui"),))) is False
    # A legacy AccountOptionField is not an opt-in until explicitly declared.
    assert valid_account_option_boolean(
        AccountOptionField("authenticated_sync", "Synchronisation")
    ) is False


def test_two_credentials_render_real_action_rows_and_keep_opt_in_isolated():
    first = stored("cred-a", "alice")
    second = stored("cred-b", "bob", option="on")
    before = deepcopy((first, second))
    a, group_a = render(first)
    b, group_b = render(second)
    toggle_a = a["option_fields"]["authenticated_sync"]
    toggle_b = b["option_fields"]["authenticated_sync"]

    assert isinstance(toggle_a.row, Toggle)
    assert toggle_a.get_text() == "off" and toggle_a.row.active is False
    assert toggle_b.get_text() == "on" and toggle_b.row.active is True
    assert a["invalid_saved_snapshot"] is False
    assert b["invalid_saved_snapshot"] is False
    assert a["probe_rendered_baseline"]["option_fields"]["authenticated_sync"] == "off"
    assert b["probe_rendered_baseline"]["option_fields"]["authenticated_sync"] == "on"
    for group in (group_a, group_b):
        options = [child for child in group.children
                   if child.title == "Synchroniser ce compte"]
        assert len(options) == 1
        assert len(options[0].suffixes) == 1
    assert (first, second) == before

    toggle_a.row.set_active(True)
    toggle_b.row.set_active(False)
    assert toggle_a.get_text() == "on"
    assert toggle_b.get_text() == "off"
    assert a["probe_rendered_baseline"]["option_fields"]["authenticated_sync"] == "off"
    assert (first, second) == before


def test_boolean_draft_persists_exact_on_off_per_account_with_legacy_options(tmp_path):
    accounts = [
        stored("cred-a", "alice"),
        stored("cred-b", "bob", option="on"),
    ]
    result = make_draft(tmp_path, accounts, changes={
        "cred-a": "on", "cred-b": "off",
    })
    persisted = {row["id"]: row for row in result.data["providers"]["accounts"]}
    assert persisted["cred-a"]["options"]["authenticated_sync"] == "on"
    assert persisted["cred-b"]["options"]["authenticated_sync"] == "off"
    assert persisted["cred-a"]["options"]["retired_opaque"] == {"old": True}
    assert persisted["cred-b"]["options"]["retired_opaque"] == {"old": True}
    assert persisted["cred-a"]["identity_id"] == "alice"
    assert persisted["cred-b"]["identity_id"] == "bob"


def test_missing_optin_defaults_off_with_no_implicit_authorization(tmp_path):
    row = stored("cred-a", "alice")
    assert "authenticated_sync" not in row["options"]
    editor, _group = render(row)
    assert editor["option_fields"]["authenticated_sync"].get_text() == "off"
    result = make_draft(tmp_path, [row])
    saved, = result.data["providers"]["accounts"]
    assert saved["options"]["authenticated_sync"] == "off"
    assert "authenticated_sync" not in row["options"]


@pytest.mark.parametrize("invalid", [
    "", "ON", "true", "yes", "1", "Activée", " off", "off ",
    True, False, 1, None, (), {"consent": True},
])
def test_invalid_or_legacy_boolean_never_becomes_optin(tmp_path, invalid):
    row = stored("cred-a", "alice")
    row["options"]["authenticated_sync"] = invalid
    editor, _ = render(row)
    assert editor["invalid_saved_snapshot"] is True
    assert editor["option_fields"]["authenticated_sync"].get_text() != "on"

    with pytest.raises(SettingsValidationError):
        make_draft(tmp_path, [row], changes={"cred-a": invalid})


@pytest.mark.parametrize("invalid_spec", [
    field(default="on"),
    field(secret=True),
    field(choices=(("off", "Non"), ("on", "Oui"))),
])
def test_invalid_boolean_descriptor_cannot_write_settings(tmp_path, invalid_spec):
    row = stored("cred-a", "alice")
    editor, _ = render(row, spec=invalid_spec)
    assert editor["invalid_saved_snapshot"] is True
    with pytest.raises(SettingsValidationError):
        make_draft(tmp_path, [row], spec=invalid_spec)


def test_poisoned_gtk_active_state_fails_closed():
    editor, _ = render(stored("cred-a", "alice"))
    control = editor["option_fields"]["authenticated_sync"]
    control.row.active = "true"
    assert control.get_text() == ""
    control.row.active = 1
    assert control.get_text() == ""
