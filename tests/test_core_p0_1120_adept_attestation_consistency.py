"""P0 #1120: Adobe-first return success requires two coherent attestations.

Only fake ADEPT/Paris adapters and a local SQLite checkpoint are exercised.
No provider ReturnDigitalLoan, actual Adobe mutation, or filesystem deletion.
"""
from __future__ import annotations

from tests.loan_return_inventory_fixtures import attest_paris_dilicom_complete

import json

import pytest

from cifereca.core.return_adept_orchestrator import (
    adept_return_key, adobe_return_attested, paris_return_attested, public_state,
)
from tests.test_core_0984_adept_first_paris_reconcile import (
    setup, stub_adept,
)


STAMP = "2026-10-19T05:42:00+00:00"
PRIVATE = "https://private.invalid/acsm?token=NEVER_SHOW"


class Hostile:
    def __bool__(self):
        raise AssertionError("Never coerce private attestation")

    def __str__(self):
        raise AssertionError("Never stringify private attestation")


@pytest.mark.parametrize(("verification", "checked_at", "adobe_verified"), [
    ("unavailable", STAMP, True),
    ("still_present", STAMP, True),
    ("adobe_confirmed", STAMP, True),
    ("provider_absent", None, True),
    ("provider_absent", PRIVATE, True),
    (PRIVATE, STAMP, True),
    ("provider_absent", STAMP, PRIVATE),
    ("provider_absent", STAMP, Hostile()),
])
def test_corrupt_final_adobe_paris_return_cannot_advertise_success(
    verification, checked_at, adobe_verified,
):
    row = {
        "status": "paris_return_confirmed",
        "verification": verification,
        "adobe_verified": adobe_verified,
        "requested_at": STAMP, "checked_at": checked_at,
        "provider_url": PRIVATE,
    }
    assert paris_return_attested(row) is False
    result = public_state(row)
    assert result["status"] in {"partial", "failed"}
    assert result["provider_return_confirmed"] is False
    assert result["paris_verification"] == "not_checked"
    assert result["retry"] == "verification_only"
    assert PRIVATE not in json.dumps(result)


@pytest.mark.parametrize("raw_status", ["request_sent", "adobe_return_requested", "failed"])
def test_pre_mutation_adobe_verified_flag_cannot_attest_return(raw_status):
    row = {"status": raw_status, "adobe_verified": True, "verification": "adobe_pending"}
    assert adobe_return_attested(row) is False
    report = public_state(row)
    assert report["adobe_return_confirmed"] is False
    assert report["provider_return_confirmed"] is False
    assert report["retry"] == "verification_only"


def test_healthy_both_phases_still_confirm_return():
    row = {
        "status": "paris_return_confirmed", "adobe_verified": True,
        "verification": "provider_absent", "checked_at": STAMP,
    }
    assert adobe_return_attested(row) is True
    assert paris_return_attested(row) is True
    result = public_state(row)
    assert result["status"] == "paris_return_confirmed"
    assert result["adobe_return_confirmed"] is True
    assert result["provider_return_confirmed"] is True
    assert result["retry"] == "none"


def test_durable_contradiction_rechecks_paris_without_replaying_adobe(tmp_path):
    service, loan, paris = setup(tmp_path)
    attest_paris_dilicom_complete(paris, credential_id="alice-paris")
    try:
        calls = stub_adept(service)
        key = adept_return_key("paris", "alice-paris", "linked-123", loan.id)
        assert service.storage.loan_return_claim(key, "old-adept-op")[0]
        old = service.storage.loan_return_transition(
            key, "old-adept-op", status="paris_return_confirmed",
            verification="still_present", adobe_verified=True,
        )
        assert old["status"] == "paris_return_confirmed"
        assert public_state(old)["provider_return_confirmed"] is False
        answer = service.return_loan_prefer_adept(loan)
        assert answer["status"] == "paris_return_confirmed"
        assert answer["paris_verification"] == "provider_absent"
        assert answer["provider_return_confirmed"] is True
        assert calls["mutations"] == 0
        assert paris.posts == 0
        assert paris.accounts_read == ["linked-123"]
        receipt = service.storage.loan_return_state(key)
        assert receipt["verification"] == "provider_absent"
        assert public_state(receipt)["provider_return_confirmed"] is True
    finally:
        service.close()


def test_pre_mutation_flag_requires_fresh_adobe_inventory_before_any_paris_check(tmp_path):
    service, loan, paris = setup(tmp_path)
    try:
        calls = stub_adept(service, already_returned=False)
        key = adept_return_key("paris", "alice-paris", "linked-123", loan.id)
        assert service.storage.loan_return_claim(key, "old-adept-op")[0]
        old = service.storage.loan_return_transition(
            key, "old-adept-op", status="adobe_return_requested",
            verification="adobe_pending", adobe_verified=True,
        )
        assert adobe_return_attested(old) is False
        answer = service.return_loan_prefer_adept(loan)
        assert answer["status"] == "adobe_return_requested"
        assert answer["adobe_return_confirmed"] is False
        assert answer["provider_return_confirmed"] is False
        assert calls["mutations"] == 0
        assert calls["inventory"] >= 1
        assert paris.posts == 0 and paris.accounts_read == []
    finally:
        service.close()
