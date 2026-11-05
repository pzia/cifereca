"""P1 #3731: Settings draft must not resurrect a provider option default.

A saved explicit blank remains blank through an unrelated Settings save, even
when the provider publishes a nonempty default. No plugin, credentials, HTTP,
account activation or file write is needed for this pure draft operation.
"""
from __future__ import annotations

from copy import deepcopy

import pytest

from cifereca.plugin_api import AccountOptionField
from tests.test_settings_draft import account, descriptor, editor, validate


def _scenario(context, *, stored, submitted, enabled=True):
    config, values = context
    saved = {
        "id": "source",
        "provider": "future-provider",
        "identity_id": "alice",
        "enabled": enabled,
        "credentials": {"username": "synthetic-user"},
        "options": {"endpoint": stored, "opaque": {"version": 2}},
    }
    config.set("providers.accounts", [deepcopy(saved)])
    draft_row = account("future-provider")
    draft_row.update(
        enabled=enabled,
        original_account=deepcopy(saved),
        original_credentials=deepcopy(saved["credentials"]),
        original_options=deepcopy(saved["options"]),
        option_fields={"endpoint": submitted},
    )
    values["accounts"] = [draft_row]
    values["identities"][0]["label"] = "Unrelated change"
    provider = descriptor("future-provider")
    provider.account_options = (
        AccountOptionField("endpoint", "Endpoint", default="https://default.example"),
    )
    before = deepcopy(config.data)
    result = validate(context, descriptors={"future-provider": provider})
    assert config.data == before
    assert not config.path.exists()
    row, = result.data["providers"]["accounts"]
    assert row["options"]["opaque"] == {"version": 2}
    assert row["enabled"] is enabled
    return row


@pytest.mark.parametrize("enabled", [True, False])
def test_existing_explicit_blank_is_not_replaced_by_descriptor_default(editor, enabled):
    row = _scenario(editor, stored="", submitted="", enabled=enabled)
    assert row["options"]["endpoint"] == ""


def test_explicit_clearing_existing_optional_option_is_persistable(editor):
    row = _scenario(editor, stored="https://prior.example", submitted="  ")
    assert row["options"]["endpoint"] == ""


def test_existing_nonempty_option_still_accepts_edit(editor):
    row = _scenario(editor, stored="", submitted=" https://new.example ")
    assert row["options"]["endpoint"] == "https://new.example"


def test_new_account_blank_uses_provider_descriptor_default(editor):
    config, values = editor
    values["accounts"] = [account("future-provider")]
    values["accounts"][0]["option_fields"] = {"endpoint": " "}
    provider = descriptor("future-provider")
    provider.account_options = (
        AccountOptionField("endpoint", "Endpoint", default="https://default.example"),
    )
    row, = validate(editor, descriptors={"future-provider": provider}).data["providers"]["accounts"]
    assert row["options"]["endpoint"] == "https://default.example"
    assert config.provider_accounts() == []
