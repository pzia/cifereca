"""#1120: provenance from ADEPT must not be coerced into a confirmed loan."""
from __future__ import annotations

import pytest

from cifereca.adept_binding import capture_binding


PRIVATE = "https://private.invalid/fulfillment.acsm?token=NEVER_PUBLISH"


class HostileValue:
    def __bool__(self):
        raise AssertionError(PRIVATE)

    def __str__(self):
        raise AssertionError(PRIVATE)

    def __eq__(self, _other):
        raise AssertionError(PRIVATE)


class HostileString(str):
    def __bool__(self):
        raise AssertionError(PRIVATE)

    def __str__(self):
        raise AssertionError(PRIVATE)

    def __eq__(self, _other):
        raise AssertionError(PRIVATE)

    def strip(self):
        raise AssertionError(PRIVATE)

    def startswith(self, *_args):
        raise AssertionError(PRIVATE)


@pytest.mark.parametrize("field,bad", [
    ("result_profile", HostileValue()),
    ("result_profile", HostileString("reader")),
    ("result_loan", HostileValue()),
    ("result_loan", HostileString("urn:uuid:private")),
    ("profile_name", HostileValue()),
    ("profile_name", HostileString("reader")),
    ("profile_id", HostileValue()),
    ("profile_id", HostileString("stable")),
    ("profile_id", True),
])
def test_untrusted_adept_provenance_cannot_be_confirmed(field, bad):
    result = {"ok": True, "profile": "reader", "loan_id": "urn:uuid:attested"}
    opts = {"profile_name": "reader", "profile_id": "stable"}
    if field == "result_profile":
        result["profile"] = bad
    elif field == "result_loan":
        result["loan_id"] = bad
    else:
        opts[field] = bad
    with pytest.raises(ValueError) as failure:
        capture_binding(result, **opts)
    assert PRIVATE not in str(failure.value)


def test_valid_adept_binding_and_no_loan_id_keep_historical_meaning():
    original = capture_binding({
        "ok": True, "profile": "reader", "loan_id": "urn:uuid:attested",
    }, profile_name="reader", profile_id="stable")
    assert original == {
        "adept_profile_id": "stable", "adept_profile_name": "reader",
        "adept_loan_id": "urn:uuid:attested", "loan_state": "active",
        "return_available": True,
    }
    without_loan = capture_binding({
        "ok": True, "profile": "reader",
    }, profile_name="reader", profile_id="stable")
    assert without_loan == {
        "adept_profile_id": "stable", "adept_profile_name": "reader",
        "adept_loan_id": None, "loan_state": "unavailable",
        "return_available": False,
    }
    without_profile_id = capture_binding({
        "ok": True, "loan_id": "urn:uuid:attested",
    }, profile_name="reader", profile_id=None)
    assert without_profile_id["loan_state"] == "active"
    assert without_profile_id["return_available"] is False
