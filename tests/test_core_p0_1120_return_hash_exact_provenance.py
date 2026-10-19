"""P0 #1120: return checkpoint hashes cannot be built from foreign objects.

Pure functions only; no SQLite writes, HTTP, ADEPT runtime, or file mutation.
"""
from __future__ import annotations

import hashlib
import json
import re

import pytest

from cifereca.core.loan_return_guard import return_key
from cifereca.core.return_adept_orchestrator import adept_return_key


class Hostile:
    def __str__(self):
        raise AssertionError("Never stringify private runtime identity")

    def __bool__(self):
        raise AssertionError("Never coerce private runtime identity")

    def __repr__(self):
        raise AssertionError("Never repr private runtime identity")


class HostileStr(str):
    def casefold(self):
        raise AssertionError("Untrusted str subclass casefold must not be called")

    def strip(self, *_args, **_kwargs):
        raise AssertionError("Untrusted str subclass strip must not be called")


@pytest.mark.parametrize("value", [
    None, True, False, 123, [], {}, Hostile(), HostileStr("private"),
])
@pytest.mark.parametrize("position", range(4))
@pytest.mark.parametrize("key_fn", [return_key, adept_return_key])
def test_return_keys_reject_untyped_source_coordinates(key_fn, position, value):
    fields = ["paris", "credential", "linked-123", "loan-123"]
    fields[position] = value
    with pytest.raises(ValueError, match="(exact|loan return needs)"):
        key_fn(*fields)


def test_original_hashes_remain_identical_for_valid_scoped_provenance():
    coordinates = ["Paris", "cred", "linked-123", "loan-123"]
    expected_pnb = "loan.return.v1:" + hashlib.sha256(json.dumps(
        ["paris", "cred", "linked-123", "loan-123"],
        ensure_ascii=False, separators=(",", ":"),
    ).encode()).hexdigest()
    expected_adept = "loan.adept.return.v1:" + hashlib.sha256(json.dumps(
        ["paris", "cred", "linked-123", "loan-123", "ADEPT"],
        ensure_ascii=False, separators=(",", ":"),
    ).encode()).hexdigest()
    assert return_key(*coordinates) == expected_pnb
    assert adept_return_key(*coordinates) == expected_adept
    assert re.fullmatch("loan.return.v1:[0-9a-f]{64}", expected_pnb)
    assert re.fullmatch("loan.adept.return.v1:[0-9a-f]{64}", expected_adept)


def test_identity_separator_and_empty_borrower_still_fail_without_side_effect():
    assert return_key("paris", "cred", "primary", "loan") != return_key(
        "paris", "cre", "dprimary", "loan"
    )
    for func in (return_key, adept_return_key):
        with pytest.raises(ValueError):
            func("paris", "cred", "", "loan")
