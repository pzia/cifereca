"""P0 #1120: public return receipts are a closed read-only projection.

Real SQLite is exercised but no POST, ADEPT runtime, GTK or network is used.
The saved checkpoint is never modified by this display helper.
"""
from __future__ import annotations

import json

import pytest

from cifereca.core.loan_return_guard import public_return_state, return_key
from cifereca.storage import Storage


PRIVATE = "https://private.invalid/loan?token=PRIVATE_BORROWER_2026"


class HostileField:
    def __str__(self):
        raise AssertionError("Untrusted status cannot be stringified")

    def __repr__(self):
        raise AssertionError("Untrusted status cannot be repr'd")

    def __bool__(self):
        raise AssertionError("Untrusted status cannot be coerced to bool")


@pytest.mark.parametrize("raw_status", [PRIVATE, {"secret": PRIVATE}, HostileField(), 1, True])
def test_untrusted_return_status_never_escapes_to_public_projection(raw_status):
    raw = {
        "status": raw_status,
        "verification": PRIVATE,
        "request_accepted": PRIVATE,
        "requested_at": PRIVATE,
        "checked_at": PRIVATE,
        "operation_id": PRIVATE,
        "borrower_id": PRIVATE,
    }
    public = public_return_state(raw)
    assert public == {
        "status": "verification_unavailable",
        "verification": "unavailable",
        "request_accepted": False,
        "provider_inventory_verified": False,
        "requested_at": None,
        "checked_at": None,
        "retry": "verification_first",
        "scope": "provider_pnb_only",
    }
    assert "PRIVATE_BORROWER_2026" not in json.dumps(public)


@pytest.mark.parametrize(("timestamp", "valid"), [
    ("2026-10-19T04:30:05+00:00", True),
    ("2026-10-19T04:30:05.123456+02:00", True),
    ("2026-10-19T04:30:05Z", True),
    ("2026-10-19T04:30:05", False),
    ("2026-99-19T04:30:05+00:00", False),
    ("1999-10-19T04:30:05+00:00", False),
    (PRIVATE, False),
    (HostileField(), False),
    (True, False),
])
def test_public_return_timestamp_requires_valid_bounded_timezone_value(timestamp, valid):
    report = public_return_state({
        "status": "pending_verification", "verification": "not_checked",
        "requested_at": timestamp, "checked_at": timestamp,
    })
    assert report["requested_at"] == (timestamp if valid else None)
    assert report["checked_at"] == (timestamp if valid else None)
    assert PRIVATE not in json.dumps(report)


def test_exact_verified_return_still_preserves_original_public_contract():
    stamp = "2026-10-19T04:30:05+00:00"
    result = public_return_state({
        "status": "provider_return_confirmed",
        "verification": "provider_absent",
        "request_accepted": True,
        "requested_at": stamp,
        "checked_at": stamp,
    })
    assert result == {
        "status": "provider_return_confirmed",
        "request_accepted": True,
        "provider_inventory_verified": True,
        "verification": "provider_absent",
        "checked_at": stamp,
        "requested_at": stamp,
        "retry": "none",
        "scope": "provider_pnb_only",
    }


def test_actual_sqlite_return_receipt_stays_unchanged_on_public_read(tmp_path):
    storage = Storage(tmp_path / "return.sqlite3")
    key = return_key("paris", "credential", "borrower", "loan")
    try:
        claimed, original = storage.loan_return_claim(key, "operation-1")
        assert claimed
        public = public_return_state(storage.loan_return_state(key))
        assert public["status"] == "request_sent"
        assert public["request_accepted"] is False
        assert public["verification"] == "not_checked"
        assert public["requested_at"] == original["requested_at"]
        updated = storage.loan_return_transition(
            key, "operation-1", status="still_active",
            verification="still_present", accepted=True,
        )
        assert updated is not None
        display = public_return_state(storage.loan_return_state(key))
        assert display["status"] == "still_active"
        assert display["request_accepted"] is True
        assert display["verification"] == "still_present"
        assert display["retry"] == "verification_first"
        assert storage.loan_return_state(key) == updated
    finally:
        storage.close()
