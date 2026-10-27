"""#3120: exact borrower+branch borrow evidence is NOT static CanLoan permission.

All account observations are synthetic. No Paris endpoint, HTTP, action or
loan acquisition is invoked or inferred.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from cifereca.models import Account
from cifereca.plugin_api.lending_capabilities import (
    BorrowerOperationEvidence, LendingOperationFact,
    assess_borrower_operation_eligibility,
)


SUPPORTED = LendingOperationFact(
    operation="loan.borrow", availability="supported",
    reason_code="available_unverified_permission",
    manifest_declared=True, descriptor_declared=True,
    method_implemented=True, session_ready=True,
    explicit_action_required=True,
)


def account(*, borrower="primary", credential="card-a"):
    return Account(
        provider_id="paris", credential_id=credential,
        identity_id="alice", unique_id=borrower,
    )


def evidence(row, **overrides):
    fields = {
        "identity_id": row.identity_id, "provider_id": row.provider_id,
        "credential_id": row.credential_id, "account_id": row.unique_id,
        "branch": "dilicom", "operation": "loan.borrow",
        "subscription": "valid", "remaining_loans": 2,
        "permission": "allowed",
    }
    return BorrowerOperationEvidence(**{**fields, **overrides})


def assess(row, observed=None, *, capability=SUPPORTED, branch="dilicom"):
    return assess_borrower_operation_eligibility(
        capability, account=row, branch=branch, evidence=observed,
    )


def test_two_accounts_can_both_support_borrow_while_both_are_blocked_differently():
    reader_a = account(borrower="reader-renewal")
    reader_b = account(borrower="reader-full")
    a = assess(reader_a, evidence(
        reader_a, subscription="renewal_pending", remaining_loans=3,
    ))
    b = assess(reader_b, evidence(
        reader_b, subscription="valid", remaining_loans=0,
    ))
    assert (a.state, a.reason_code) == (
        "temporarily_blocked", "subscription_not_borrowable",
    )
    assert (b.state, b.reason_code) == (
        "temporarily_blocked", "quota_reached",
    )
    assert SUPPORTED.availability == "supported"
    assert SUPPORTED.reason_code == "available_unverified_permission"
    assert reader_a.credential_id == reader_b.credential_id == "card-a"


@pytest.mark.parametrize("scope", [
    {"identity_id": "bob"}, {"provider_id": "bnf-pret"},
    {"credential_id": "foreign-card"}, {"account_id": "other-reader"},
    {"branch": "syracuse"}, {"operation": "loan.return"},
])
def test_other_account_or_backend_status_cannot_block_or_allow_target(scope):
    reader = account()
    result = assess(reader, evidence(reader, subscription="renewal_pending", **scope))
    assert (result.state, result.reason_code) == (
        "unverified", "borrower_scope_unverified",
    )


@pytest.mark.parametrize("inputs,expected", [
    ({"subscription": "unknown"}, "borrow_permission_unverified"),
    ({"remaining_loans": None}, "borrow_permission_unverified"),
    ({"permission": "unknown"}, "borrow_permission_unverified"),
    ({"subscription": "expired"}, "subscription_not_borrowable"),
    ({"permission": "denied"}, "borrow_permission_denied"),
    ({"subscription": "unknown", "remaining_loans": 0}, "quota_reached"),
])
def test_independently_attested_blocker_overrides_unknown_but_unknown_cannot_allow(
    inputs, expected,
):
    reader = account()
    status = assess(reader, evidence(reader, **inputs))
    assert status.reason_code == expected
    assert status.state == (
        "temporarily_blocked" if expected in {
            "subscription_not_borrowable", "borrow_permission_denied",
            "quota_reached",
        } else "unverified"
    )


def test_complete_exact_positive_observation_is_not_remote_authorization():
    reader = account()
    status = assess(reader, evidence(reader))
    assert (status.state, status.reason_code) == (
        "eligible_observed", "borrow_permission_observed",
    )
    assert not hasattr(status, "usable")
    # Neither a method nor a provider session is constructed or called.


@pytest.mark.parametrize("field", [
    {"remaining_loans": -1}, {"remaining_loans": True},
    {"remaining_loans": 10001}, {"subscription": "PRIVATE_UNKNOWN"},
    {"subscription": []}, {"permission": []}, {"permission": "true"},
])
def test_invalid_provider_evidence_is_never_promoted(field):
    reader = account()
    status = assess(reader, evidence(reader, **field))
    assert (status.state, status.reason_code) == (
        "unverified", "borrower_evidence_invalid",
    )


def test_no_evidence_unavailable_capability_and_wrong_operation_fail_closed():
    reader = account()
    assert assess(reader).state == "unverified"
    assert assess(reader, evidence(reader), branch="syracuse").state == "unverified"
    assert assess(reader, evidence(reader), capability=replace(
        SUPPORTED, availability="unsupported",
    )).state == "unsupported"
    assert assess(reader, evidence(reader), capability=replace(
        SUPPORTED, availability="unavailable",
    )).state == "unavailable"
    assert assess(reader, evidence(reader), capability=replace(
        SUPPORTED, operation="loan.list",
    )).state == "unverified"
    assert assess(reader, evidence(reader), capability=replace(
        SUPPORTED, session_ready=False,
    )).state == "unverified"
