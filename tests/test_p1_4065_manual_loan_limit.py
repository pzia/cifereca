"""#4065: advisory BnF loan ceiling is strictly declarative, never server quota."""
from copy import deepcopy

import pytest

from cifereca.plugin_api import AccountOptionField
from cifereca.gui.settings_draft import SettingsValidationError

from test_settings_draft import account, descriptor, validate


LIMIT = AccountOptionField(
    "manual_loan_limit", "Maximum d’emprunts (valeur personnelle)",
    default="4", value_type="unsigned_integer",
    min_value=0, max_value=99,
)


@pytest.mark.parametrize("value", ["0", "1", "4", "50", "99"])
def test_valid_advisory_bnf_limit_preserves_other_account_options(editor, value):
    config, values = editor
    before = deepcopy(config.data)
    provider = descriptor("bnf-pret")
    provider.account_options = (LIMIT,)
    row = account("bnf-pret")
    row["original_options"] = {"opaque_future_field": {"unchanged": True}}
    row["option_fields"] = {"manual_loan_limit": value}
    values["accounts"] = [row]
    saved = validate(editor, descriptors={"bnf-pret": provider})
    assert config.data == before
    options = saved.data["providers"]["accounts"][0]["options"]
    assert options["manual_loan_limit"] == value
    assert options["opaque_future_field"] == {"unchanged": True}


@pytest.mark.parametrize("invalid", [
    "", " 4", "4 ", "04", "000", "-1", "+4", "1.0", "1e1",
    "100", "999", "0x4", "true", None, True, False, 4, {}, [],
])
def test_invalid_advisory_limit_rejected_without_silent_default(editor, invalid):
    config, values = editor
    original = deepcopy(config.data)
    provider = descriptor("bnf-pret")
    provider.account_options = (LIMIT,)
    row = account("bnf-pret")
    row["option_fields"] = {"manual_loan_limit": invalid}
    values["accounts"] = [row]
    with pytest.raises(SettingsValidationError):
        validate(editor, descriptors={"bnf-pret": provider})
    assert config.data == original


def test_missing_option_in_old_editor_never_grants_or_invents_a_quota(editor):
    provider = descriptor("bnf-pret")
    provider.account_options = (LIMIT,)
    row = account("bnf-pret")
    row["option_fields"] = {}  # A legacy editor did not know the new field.
    editor[1]["accounts"] = [row]
    result = validate(editor, descriptors={"bnf-pret": provider})
    assert "manual_loan_limit" not in result.data["providers"]["accounts"][0]["options"]
