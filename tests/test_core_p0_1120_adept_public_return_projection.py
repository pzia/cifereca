"""P0 #1120: Adobe-first return receipts never reveal private checkpoint data.

Only a read-only public projection and temporary SQLite are exercised. No
Adobe runtime, Paris network, credentials or real borrower mutation is used.
"""
from __future__ import annotations

import json

import pytest

from cifereca.core.return_adept_orchestrator import adept_return_key, public_state
from cifereca.storage import Storage

PRIVATE = "https://private.invalid/return?token=DO_NOT_LOG_2026"


class HostileValue:
    def __str__(self):
        raise AssertionError("An untrusted checkpoint field was stringified")

    def __repr__(self):
        raise AssertionError("An untrusted checkpoint field was repr'd")

    def __bool__(self):
        raise AssertionError("An untrusted checkpoint field was coerced")


@pytest.mark.parametrize("raw_status", [PRIVATE, HostileValue(), {"url": PRIVATE}, True, 17])
def test_unknown_adobe_checkpoint_status_is_fail_closed_and_not_exported(raw_status):
    projection = public_state({
        "status": raw_status,
        "verification": PRIVATE,
        "adobe_verified": True,
        "requested_at": PRIVATE,
        "checked_at": HostileValue(),
        "borrower": PRIVATE,
    })
    assert projection == {
        "status": "failed",
        "scope": "adobe_first_paris",
        "adobe_return_confirmed": False,
        "provider_return_confirmed": False,
        "paris_verification": "not_checked",
        "requested_at": None,
        "checked_at": None,
        "retry": "verification_only",
    }
    assert "DO_NOT_LOG_2026" not in json.dumps(projection)


@pytest.mark.parametrize("value", [PRIVATE, HostileValue(), {"private": PRIVATE}, True])
def test_unknown_adobe_verification_does_not_escape_or_claim_inventory(value):
    projection = public_state({
        "status": "partial", "adobe_verified": True,
        "verification": value,
    })
    assert projection["status"] == "partial"
    assert projection["adobe_return_confirmed"] is True
    assert projection["paris_verification"] == "not_checked"
    assert projection["retry"] == "verification_only"
    assert PRIVATE not in json.dumps(projection)


@pytest.mark.parametrize(("timestamp", "valid"), [
    ("2026-10-19T04:30:05+00:00", True),
    ("2026-10-19T04:30:05.123456+02:00", True),
    ("2026-10-19T04:30:05Z", True),
    ("2026-10-19T04:30:05", False),
    ("2026-99-19T04:30:05+00:00", False),
    (PRIVATE, False),
    (HostileValue(), False),
])
def test_adobe_projection_accepts_only_bounded_timezone_aware_dates(timestamp, valid):
    projection = public_state({
        "status": "adobe_return_requested",
        "verification": "adobe_pending",
        "requested_at": timestamp, "checked_at": timestamp,
    })
    assert projection["requested_at"] == (timestamp if valid else None)
    assert projection["checked_at"] == (timestamp if valid else None)


def test_confirmed_historical_adobe_paris_checkpoint_stays_confirmed():
    stamp = "2026-10-19T04:30:05+00:00"
    result = public_state({
        "status": "paris_return_confirmed",
        "adobe_verified": True, "verification": "provider_absent",
        "requested_at": stamp, "checked_at": stamp,
    })
    assert result == {
        "status": "paris_return_confirmed",
        "scope": "adobe_first_paris",
        "adobe_return_confirmed": True,
        "provider_return_confirmed": True,
        "paris_verification": "provider_absent",
        "requested_at": stamp, "checked_at": stamp,
        "retry": "none",
    }


def test_corrupt_confirmation_without_adobe_proof_never_reports_final_success():
    result = public_state({
        "status": "paris_return_confirmed",
        "adobe_verified": PRIVATE, "verification": "provider_absent",
    })
    assert result["adobe_return_confirmed"] is False
    assert result["provider_return_confirmed"] is False
    assert result["retry"] == "verification_only"
    assert PRIVATE not in json.dumps(result)


def test_adobe_partial_still_requires_explicit_provider_action():
    result = public_state({
        "status": "partial",
        "adobe_verified": True, "verification": "still_present",
    })
    assert result["retry"] == "provider_action_explicit"
    assert result["adobe_return_confirmed"] is True
    assert result["provider_return_confirmed"] is False


def test_real_sqlite_adobe_return_projection_does_not_modify_checkpoint(tmp_path):
    storage = Storage(tmp_path / "offline.sqlite3")
    key = adept_return_key("paris", "source", "borrower", "book")
    try:
        claimed, original = storage.loan_return_claim(key, "offline-operation")
        assert claimed
        result = public_state(storage.loan_return_state(key))
        assert result["status"] == "request_sent"
        assert result["adobe_return_confirmed"] is False
        updated = storage.loan_return_transition(
            key, "offline-operation", status="adobe_return_requested",
            verification="adobe_pending", adobe_verified=False,
        )
        assert updated is not None
        projection = public_state(storage.loan_return_state(key))
        assert projection["status"] == "adobe_return_requested"
        assert projection["provider_return_confirmed"] is False
        assert storage.loan_return_state(key) == updated
    finally:
        storage.close()
