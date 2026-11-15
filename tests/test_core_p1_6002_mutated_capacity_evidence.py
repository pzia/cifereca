"""P1 #6002: frozen evidence can be mutated after constructor validation.

The evaluator must fail closed, while a healthy independent quota remains
usable. These tests never grant eligibility, persist a receipt or contact a
provider.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime

import pytest

from tests.test_core_p1_6002_remaining_policy import (
    NOW, SCOPE, decision, inventory, provider,
)


@pytest.mark.parametrize(("field", "corrupted"), [
    ("value", True),
    ("value", -1),
    ("value", 10001),
    ("value", "2"),
    ("kind", 1),
    ("kind", "unavailable"),
    ("epoch", True),
    ("epoch", "1"),
    ("observed_at", "2026-11-14"),
    ("observed_at", datetime(2026, 11, 14)),
    ("scope", "not-a-scope"),
])
def test_mutated_direct_provider_fact_never_admits_remaining(field, corrupted):
    fact = provider("remaining", 2)
    object.__setattr__(fact, field, corrupted)
    got = decision(provider_remaining=fact)
    assert (got.status, got.remaining, got.eligibility) == (
        "unknown", None, "not_verified",
    )


@pytest.mark.parametrize(("field", "corrupted"), [
    ("active_loan_ids", ["loan"]),
    ("active_loan_ids", ("good", "")),
    ("active_loan_ids", ("good", None)),
    ("status", True),
    ("status", "complete"),
    ("epoch", True),
    ("observed_at", "not-a-date"),
    ("pagination_complete", 1),
    ("durably_committed", 1),
    ("scope", {}),
])
def test_mutated_inventory_fact_never_admits_manual_or_provider_calculation(
    field, corrupted,
):
    receipt = inventory()
    object.__setattr__(receipt, field, corrupted)
    for extra in ({"manual_total": 4},
                  {"manual_total": 4, "provider_total": provider("total", 5)}):
        got = decision(inventory=receipt, **extra)
        assert (got.status, got.remaining, got.eligibility) == (
            "unknown", None, "not_verified",
        )


def test_mutated_provider_total_is_ignored_but_valid_manual_estimate_remains():
    total = provider("total", 3)
    object.__setattr__(total, "value", True)
    got = decision(provider_total=total, manual_total=4, inventory=inventory())
    assert (got.status, got.remaining, got.reason) == (
        "manual_estimate", 2, "personal_ceiling",
    )


def test_mutated_direct_fact_falls_back_to_independent_valid_provider_total():
    direct = provider("remaining", 2)
    object.__setattr__(direct, "observed_at", object())
    got = decision(provider_remaining=direct,
                   provider_total=provider("total", 5), inventory=inventory())
    assert (got.status, got.remaining, got.reason) == (
        "provider_calculated", 3, "provider_total",
    )


def test_mutated_nested_scope_is_rejected_even_if_record_still_matches_by_identity():
    poisoned_scope = replace(SCOPE)
    direct = replace(provider("remaining", 2), scope=poisoned_scope)
    object.__setattr__(poisoned_scope, "identity_id", "")
    assert decision(provider_remaining=direct).remaining is None
    receipt = inventory(scope=poisoned_scope)
    assert decision(manual_total=4, inventory=receipt).remaining is None


@pytest.mark.parametrize(("field", "bad"), [
    ("identity_id", ""),
    ("provider_id", None),
    ("branch", "physical"),
    ("account_id", 42),
])
def test_mutated_input_scope_raises_closed_context_error(field, bad):
    poisoned = replace(SCOPE)
    object.__setattr__(poisoned, field, bad)
    with pytest.raises(ValueError, match="invalid_capacity_evaluation_context"):
        decision(scope=poisoned)


def test_valid_signed_timezone_and_complete_evidence_still_use_precedence():
    got = decision(provider_remaining=provider("remaining", 0),
                   provider_total=provider("total", 6),
                   manual_total=4, inventory=inventory())
    assert (got.status, got.remaining, got.eligibility) == (
        "provider_direct", 0, "not_verified",
    )
