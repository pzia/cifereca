"""P0 #5851: the public loan lock cannot trust plugin coordinates."""
from types import SimpleNamespace as NS

import pytest

from cifereca.services import LibraryService


class Hostile:
    def __str__(self):
        raise AssertionError("plugin source must not be coerced")

    def __bool__(self):
        raise AssertionError("plugin source must not be truth-tested")


class HostileString(str):
    def strip(self, *args):
        raise AssertionError("plugin string subclass must not normalize the lock")

    def lower(self):
        raise AssertionError("plugin string subclass must not select a source")


@pytest.mark.parametrize("field,value", [
    ("id", Hostile()),
    ("id", HostileString("loan")),
    ("id", None),
    ("id", ""),
    ("source_provider", Hostile()),
    ("source_provider", HostileString("paris")),
    ("source_provider", None),
    ("source_provider", ""),
    ("source_credential", Hostile()),
    ("source_credential", HostileString("account")),
    ("source_credential", None),
    ("source_credential", ""),
])
def test_outer_automatic_loan_lock_refuses_untrusted_scope_without_execution(
    field, value,
):
    loan = NS(id="loan", source_provider="paris", source_credential="account")
    setattr(loan, field, value)

    def forbidden(*args, **kwargs):
        pytest.fail("invalid loan must not acquire locks, touch storage or stage ACSM")

    service = NS(_acquire_active_loan_automatically_guarded=forbidden)
    result = LibraryService._acquire_active_loan_automatically(service, loan)
    assert result == {
        "status": "attention_required", "loan_id": None,
        "code": "adept_loan_identity_unproven", "retry": "explicit_only",
    }
