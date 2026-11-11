"""#5055: obsolete Adobe return-recovery API cannot regain mutation authority.

The historical recovery predicate was removed with voluntary ADEPT returns.
Only the public, strictly inert rejection boundary remains.
"""
from __future__ import annotations

import pytest

from cifereca.core import loan_return_verification
from cifereca.plugin_api import AttentionRequired
from cifereca.services import LibraryService


class Hostile:
    def __getattribute__(self, name):
        raise AssertionError("removed return reached provider or storage")

    def __bool__(self):
        raise AssertionError("removed return coerced external data")

    def __str__(self):
        raise AssertionError("removed return serialized external data")


def test_removed_adobe_recovery_exposes_no_bypass():
    assert not hasattr(loan_return_verification, "adobe_return_inventory_confirmed")
    assert not hasattr(LibraryService, "_adobe_return_inventory_confirmed")


@pytest.mark.parametrize("name", (
    "return_adept_acquisition", "_return_adept_acquisition",
))
def test_retired_return_rejects_hostile_acquisition_before_any_io(name):
    with pytest.raises(AttentionRequired) as error:
        getattr(LibraryService, name)(Hostile(), Hostile())
    assert error.value.code == "loan_return_removed"
