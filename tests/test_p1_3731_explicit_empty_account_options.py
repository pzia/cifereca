"""#3731/#3198 — Explicit per-account option values outrank retired defaults.

GTK-free provider-neutral fixtures.  No network, credentials, consent, source
activation or persistent configuration mutations are performed.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from cifereca.gui.settings_account_editor import build_provider_account_editor
from tests.test_core_ui_1127_provider_account_editor import Widget, _render, _url_option


OPTION = _url_option(
    "endpoint", "Option facultative du compte",
    "https://descriptor.example", "deprecated.endpoint",
)


def test_explicit_empty_value_is_not_resurrected_from_legacy_or_descriptor():
    saved = {
        "id": "future-account", "enabled": False,
        "options": {"endpoint": "", "opaque-v2": "keep"},
    }
    editor, _, _, _ = _render(
        "future-provider", data=saved, descriptor_options=[OPTION],
        provider_values={("future-provider", "endpoint", "future-account"): "https://provider.example"},
        legacy_values={"deprecated.endpoint": "https://global.example"},
    )
    assert editor["option_fields"]["endpoint"].text == ""
    assert editor["original_options"] == {"endpoint": "", "opaque-v2": "keep"}
    assert editor["invalid_saved_snapshot"] is False
    assert editor["enabled"].active is False
    assert saved["options"]["endpoint"] == ""


def test_explicit_empty_option_does_not_even_read_retired_config():
    def forbidden(*_args, **_kwargs):
        raise AssertionError("unexpected legacy/global option read")

    group = Widget()
    descriptor = NS(
        display_name="Future provider", credential_fields=(),
        account_options=[OPTION], account_note=None,
    )
    gtk = NS(Switch=Widget, Button=lambda label=None: Widget(),
             Align=NS(CENTER="center"))
    adw = NS(
        PreferencesGroup=Widget, ActionRow=Widget,
        EntryRow=Widget, PasswordEntryRow=Widget,
    )
    editor, _ = build_provider_account_editor(
        data={"id": "future-account", "enabled": False,
              "options": {"endpoint": ""}},
        provider_id="future-provider", account_id="future-account",
        descriptor=descriptor, configured_identities=[{"id": "reader"}],
        accounts_container=group,
        config=NS(provider_option=forbidden, get=forbidden),
        Gtk=gtk, Adw=adw,
    )
    assert editor["option_fields"]["endpoint"].text == ""
    assert editor["enabled"].active is False


@pytest.mark.parametrize("malformed", [None, False, True, 42, []])
def test_malformed_explicit_value_is_not_hidden_by_plausible_legacy_default(malformed):
    editor, _, _, _ = _render(
        "future-provider",
        data={"id": "future-account", "options": {"endpoint": malformed}},
        descriptor_options=[OPTION],
        legacy_values={"deprecated.endpoint": "https://global.example"},
    )
    assert editor["invalid_saved_snapshot"] is True
    assert editor["option_fields"]["endpoint"].text == ""
    assert editor["original_options"]["endpoint"] is malformed


def test_missing_option_still_uses_declared_legacy_fallback_in_correct_order():
    from_provider, _, _, _ = _render(
        "future-provider", data={"id": "future-account", "options": {}},
        descriptor_options=[OPTION],
        provider_values={("future-provider", "endpoint", "future-account"): "https://provider.example"},
        legacy_values={"deprecated.endpoint": "https://global.example"},
    )
    assert from_provider["option_fields"]["endpoint"].text == "https://provider.example"
    from_global, _, _, _ = _render(
        "future-provider", data={"id": "legacy-account", "options": {}},
        descriptor_options=[OPTION],
        legacy_values={"deprecated.endpoint": "https://global.example"},
    )
    assert from_global["option_fields"]["endpoint"].text == "https://global.example"
    fresh, _, _, _ = _render(
        "future-provider", descriptor_options=[OPTION],
        legacy_values={"deprecated.endpoint": "https://global.example"},
    )
    assert fresh["option_fields"]["endpoint"].text == "https://descriptor.example"
    from_descriptor, _, _, _ = _render("future-provider", descriptor_options=[OPTION])
    assert from_descriptor["option_fields"]["endpoint"].text == "https://descriptor.example"
