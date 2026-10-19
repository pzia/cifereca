"""P0 #1120: Adobe crash recovery must reject corrupt legacy binding before I/O.

No real ADEPT registry, return mutation, HTTP, PNB POST, account or filesystem.
The actual Core recovery predicate is exercised with injected local seams.
"""
from __future__ import annotations

import json
from types import SimpleNamespace as NS

import pytest

from cifereca.core.loan_return_verification import adobe_return_inventory_confirmed


PRIVATE = "https://private.invalid/loan.acsm?token=NEVER_EXPOSE_ACCOUNT"


class Hostile:
    def __bool__(self):
        raise AssertionError("Never coerce hostile ADEPT binding data")

    def __str__(self):
        raise AssertionError("Never stringify ADEPT binding data")

    def __eq__(self, _other):
        raise AssertionError("Never compare ADEPT binding objects")


def _facade(inventory):
    observed = []
    def profiles():
        observed.append("profiles")
        return [{"profile_id": "original-profile"}]
    def profile_row(profile_id, rows):
        observed.append("resolve")
        assert profile_id == "original-profile"
        return rows[0]
    def profile_name(_row):
        observed.append("name")
        return "named-original"
    def adapter():
        observed.append("adapter")
        return NS(loans=lambda name: (
            observed.append("inventory"), inventory
        )[1])
    service = NS(
        kobo_profiles=profiles,
        _adept_profile_row=profile_row,
        _adept_profile_name=profile_name,
        _kobo_adapter=adapter,
    )
    return service, observed


GOOD = {
    "metadata": {
        "adept_binding": {
            "adept_profile_id": "original-profile",
            "adept_loan_id": "exact-adobe-loan",
        }
    }
}


@pytest.mark.parametrize("acquisition", [
    {}, {"metadata": None},
    {"metadata": PRIVATE}, {"metadata": [PRIVATE]},
    {"metadata": {"adept_binding": PRIVATE}},
    {"metadata": {"adept_binding": [PRIVATE]}},
    {"metadata": {"adept_binding": Hostile()}},
    {"metadata": {"adept_binding": {"adept_profile_id": Hostile(),
                                      "adept_loan_id": "exact-adobe-loan"}}},
    {"metadata": {"adept_binding": {"adept_profile_id": "original-profile",
                                      "adept_loan_id": Hostile()}}},
    {"metadata": {"adept_binding": {"adept_profile_id": "original-profile",
                                      "adept_loan_id": True}}},
    {"metadata": {"adept_binding": {"adept_profile_id": "original-profile",
                                      "adept_loan_id": "x" * 257}}},
    [PRIVATE],
])
def test_invalid_original_adobe_binding_never_reaches_external_registry(acquisition):
    service, observed = _facade(
        {"loans": [{"id": "exact-adobe-loan", "returned": True,
                     "token_present": False}]},
    )
    assert adobe_return_inventory_confirmed(service, acquisition) is False
    assert observed == []  # no profile list or adapter resolution


@pytest.mark.parametrize(("inventory", "attested"), [
    ({"loans": [{"id": "exact-adobe-loan", "returned": True,
                  "token_present": False}]}, True),
    ({"loans": [{"id": "exact-adobe-loan", "returned": True,
                  "token_present": False},
                 {"id": "exact-adobe-loan", "returned": True,
                  "token_present": False}]}, False),
    ({"loans": [{"id": "foreign", "returned": True, "token_present": False}]}, False),
    ({"loans": [{"id": "exact-adobe-loan", "returned": "true",
                  "token_present": False}]}, False),
    ({"loans": [{"id": "exact-adobe-loan", "returned": True,
                  "token_present": "false"}]}, False),
    ({"loans": [{"id": "exact-adobe-loan", "returned": True,
                  "token_present": True}]}, False),
    ({"loans": [{"id": "exact-adobe-loan", "returned": True,
                  "token_present": False}, PRIVATE]}, True),
    ({"loans": [Hostile()]}, False),
    ({"loans": {"id": "exact-adobe-loan", "returned": True}}, False),
    ({"loans": PRIVATE}, False),
    ({}, False),
    (PRIVATE, False),
])
def test_return_confirmation_requires_exact_single_verified_registry_row(
    inventory, attested,
):
    service, observed = _facade(inventory)
    assert adobe_return_inventory_confirmed(service, GOOD) is attested
    assert observed == ["profiles", "resolve", "name", "adapter", "inventory"]
