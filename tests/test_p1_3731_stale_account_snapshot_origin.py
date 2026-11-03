"""P1 #3731: an unrelated Settings save must not transplant account metadata.

A stale or duplicate GTK row can refer to one provider connection while its
original-account snapshot describes another. The immutable original is only
safe to copy when the exact connection and provider still match.
"""
from __future__ import annotations

from copy import deepcopy

import pytest

from cifereca.gui.settings_draft import SettingsValidationError
from tests.test_settings_draft import account, descriptor, editor, validate


def _stored(id, provider="future-provider"):
    return {
        "id": id, "provider": provider, "identity_id": "alice",
        "enabled": True, "credentials": {"username": id},
        "options": {"endpoint": f"https://{id}.example"},
        "opaque_provider_extension": {"owner": id, "never_copy": True},
    }


def _row(stored):
    row = account("future-provider", account_id=stored["id"])
    row.update(
        enabled=True, original_account=deepcopy(stored),
        original_credentials=deepcopy(stored["credentials"]),
        original_options=deepcopy(stored["options"]),
        fields={"username": stored["credentials"]["username"]},
    )
    return row


@pytest.mark.parametrize("wrong", ["id", "provider", "nontext_provider"])
def test_stale_account_snapshot_refuses_cross_connection_extension_copy(
    editor, wrong,
):
    config, values = editor
    first, second = _stored("first"), _stored("second")
    config.set("providers.accounts", [deepcopy(first), deepcopy(second)])
    good, stale = _row(first), _row(second)
    if wrong == "id":
        stale["original_account"] = deepcopy(first)
        stale["original_credentials"] = deepcopy(first["credentials"])
        stale["original_options"] = deepcopy(first["options"])
    elif wrong == "provider":
        stale["original_account"]["provider"] = "another-provider"
    else:
        stale["original_account"]["provider"] = 17
    values["accounts"] = [good, stale]
    values["identities"][0]["label"] = "Unrelated edit"
    before = deepcopy(config.data)

    with pytest.raises(SettingsValidationError, match="Origine du compte"):
        validate(
            editor,
            descriptors={"future-provider": descriptor("future-provider")},
        )
    assert config.data == before
    assert config.provider_accounts() == [first, second]
    assert not config.path.exists()


def test_exact_source_keeps_opaque_fields_and_legacy_provider_case(editor):
    config, values = editor
    first = _stored("first", provider="FUTURE-PROVIDER")
    config.set("providers.accounts", [deepcopy(first)])
    values["accounts"] = [_row(first)]
    result = validate(
        editor, descriptors={"future-provider": descriptor("future-provider")},
    )
    saved, = result.data["providers"]["accounts"]
    assert saved["id"] == "first"
    assert saved["provider"] == "future-provider"
    assert saved["opaque_provider_extension"] == first["opaque_provider_extension"]
    assert config.provider_accounts() == [first]
    assert not config.path.exists()


def test_new_account_without_historical_snapshot_remains_supported(editor):
    config, values = editor
    values["accounts"] = [account("future-provider", account_id="brand-new")]
    saved, = validate(
        editor, descriptors={"future-provider": descriptor("future-provider")},
    ).data["providers"]["accounts"]
    assert saved["id"] == "brand-new"
    assert saved["credentials"]["username"] == "synthetic-user"
    assert not config.path.exists()
