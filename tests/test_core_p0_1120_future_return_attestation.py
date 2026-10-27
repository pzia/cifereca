"""P0 #1120: PNB and Adobe-first returns cannot trust future inventory proof.

Synthetic clocks, synthetic Paris inventory and local SQLite checkpoints only.
No provider return POST, ADEPT mutation, HTTP or local artifact deletion.
"""
from __future__ import annotations

from tests.loan_return_inventory_fixtures import attest_paris_dilicom_complete

import json
from datetime import datetime, timedelta, timezone

import pytest

from cifereca.core.loan_return_guard import (
    _verification_time_attested, provider_absence_attested, public_return_state,
    return_key,
)
from cifereca.core.return_adept_orchestrator import (
    adept_return_key, paris_return_attested, public_state,
)
from tests.test_core_0982_loan_return_reconciliation import (
    _library, _loan, Session,
)
from tests.test_core_0984_adept_first_paris_reconcile import (
    setup as adept_setup, stub_adept,
)


class HostileTime:
    def __str__(self):
        raise AssertionError("Timestamp must never be stringified")

    def __eq__(self, _other):
        raise AssertionError("Timestamp must never be compared")


@pytest.mark.parametrize(("delta", "attested"), [
    (-86400 * 365, True),
    (-1, True),
    (0, True),
    (179, True),
    (180, True),
    (181, False),
    (86400, False),
])
def test_valid_timezone_checked_at_is_bounded_against_future_skew(delta, attested):
    now = datetime(2026, 10, 19, 7, 0, 0, tzinfo=timezone.utc)
    value = (now + timedelta(seconds=delta)).isoformat()
    assert _verification_time_attested(value, now=now) is attested


@pytest.mark.parametrize("value", [
    None, True, 1, HostileTime(),
    "2026-10-20T07:00:00",  # no timezone
    "https://private.invalid/?token=PRIVATE_ACSM",
    "2101-01-01T00:00:00+00:00",
])
def test_invalid_or_private_timestamp_does_not_attest(value):
    now = datetime(2026, 10, 19, 7, 0, 0, tzinfo=timezone.utc)
    assert _verification_time_attested(value, now=now) is False


def test_future_confirmed_pnb_checkpoint_is_read_only_rechecked_without_post(tmp_path):
    session = Session([])
    attest_paris_dilicom_complete(session, credential_id="paris-a")
    service = _library(tmp_path, session=session)
    loan = _loan()
    key = return_key("paris", "paris-a", "linked-b", loan.id)
    try:
        assert service.storage.loan_return_claim(key, "future-pnb-op")[0]
        row = service.storage.loan_return_transition(
            key, "future-pnb-op", status="provider_return_confirmed",
            verification="provider_absent", accepted=True,
        )
        row["checked_at"] = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        service.storage.metadata_set(key, json.dumps(row))
        assert provider_absence_attested(row) is False
        display = public_return_state(row)
        assert display["provider_inventory_verified"] is False
        assert display["status"] == "verification_unavailable"
        assert service.storage.loan_return_state(key) == row

        refreshed = service.verify_return_digital(loan)
        assert refreshed["status"] == "provider_return_confirmed"
        assert refreshed["provider_inventory_verified"] is True
        assert refreshed["verification"] == "provider_absent"
        assert session.posts == []
        assert session.checks == ["linked-b"]
        assert service.return_digital(loan)["provider_inventory_verified"] is True
        assert session.posts == []
    finally:
        service.close()


def test_future_confirmed_adobe_first_checkpoint_rechecks_paris_without_replaying_adept(
    tmp_path,
):
    service, loan, paris = adept_setup(tmp_path)
    attest_paris_dilicom_complete(paris, credential_id="alice-paris")
    try:
        calls = stub_adept(service)
        key = adept_return_key("paris", "alice-paris", "linked-123", loan.id)
        assert service.storage.loan_return_claim(key, "future-adobe-op")[0]
        row = service.storage.loan_return_transition(
            key, "future-adobe-op", status="paris_return_confirmed",
            verification="provider_absent", adobe_verified=True,
        )
        row["checked_at"] = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        service.storage.metadata_set(key, json.dumps(row))
        assert paris_return_attested(row) is False
        display = public_state(row)
        assert display["provider_return_confirmed"] is False
        assert display["retry"] == "verification_only"
        assert service.storage.loan_return_state(key) == row

        fresh = service.return_loan_prefer_adept(loan)
        assert fresh["status"] == "paris_return_confirmed"
        assert fresh["provider_return_confirmed"] is True
        assert fresh["paris_verification"] == "provider_absent"
        assert calls["mutations"] == 0
        assert paris.posts == 0
        assert paris.accounts_read == ["linked-123"]
    finally:
        service.close()
