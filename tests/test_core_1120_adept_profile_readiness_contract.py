"""Core P0 #1120 — characterize explicit ADEPT ACSM readiness precedence."""
from __future__ import annotations

from copy import deepcopy

import pytest

from cifereca.core.adept_acsm_plan import profile_is_explicitly_acsm_unready


@pytest.mark.parametrize(
    ("row", "blocked"),
    [
        (None, False),
        ({}, False),
        ({"profile_id": "legacy"}, False),
        ({"fetch_capable": True}, False),
        ({"fetch_capable": False}, True),
        ({"fetch_capable": None}, False),
        ({"fetch_capable": 0}, False),
        ({"fetch_capable": "false"}, False),
        ({"fetch_capable": True, "provisioning": {"acsm_fetch_ready": False}}, True),
        ({"fetch_capable": False, "provisioning": {"acsm_fetch_ready": True}}, False),
        ({"fetch_capable": False, "provisioning": {"acsm_fetch_ready": None}}, False),
        ({"fetch_capable": False, "provisioning": {"acsm_fetch_ready": 0}}, False),
        ({"fetch_capable": False, "provisioning": {"reader_backed_identity_ready": True}}, True),
        ({"fetch_capable": False, "provisioning": None}, True),
        ({"fetch_capable": False, "provisioning": []}, True),
        ({"fetch_capable": True, "provisioning": {"reader_backed_identity_ready": False}}, False),
        ({"fetch_capable": True, "provisioning": {"acsm_fetch_ready": False, "reader_backed_identity_ready": True}}, True),
    ],
)
def test_only_explicit_boolean_false_blocks_and_new_contract_takes_precedence(row, blocked):
    original = deepcopy(row)
    assert profile_is_explicitly_acsm_unready(row) is blocked
    assert row == original


def test_readiness_probe_never_mutates_profile_or_infers_another_identity():
    profile = {
        "profile_id": "borrower-primary",
        "name": "borrower-profile",
        "fetch_capable": True,
        "provisioning": {"acsm_fetch_ready": False},
        "bound_to": {"identity_id": "other-account"},
    }
    before = deepcopy(profile)
    assert profile_is_explicitly_acsm_unready(profile) is True
    assert profile == before
    assert profile["profile_id"] == "borrower-primary"
