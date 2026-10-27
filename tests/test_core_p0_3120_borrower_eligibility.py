"""#3120: an advertised borrow method is NOT per-borrower permission.

All evidence below is synthetic, bound to one exact borrower/branch/edition.
No plugin GET, loan, cache or SQLite mutation is involved.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from cifereca.models import Account, SearchResult
from cifereca.plugin_api import (
    BorrowerOperationEvidence, assess_borrower_eligibility,
)
from cifereca.plugin_api.lending_capabilities import LendingOperationFact


def account(*, credential="card-A", borrower="reader-A", identity="alice",
            provider="paris"):
    return Account(provider_id=provider, credential_id=credential,
                   unique_id=borrower, identity_id=identity)


def notice(*, credential="card-A", provider="paris", docbase="DILICOM",
           record="digital-edition-1", collection="ebooks"):
    return SearchResult(
        id="notice-exact", record_id=record, docbase=docbase, medium="digital",
        title="Même titre", author=None, type=None, format=None, publisher=None,
        published_at=None, description=None, thumbnail_url=None,
        friendly_url=None, scenario="synthetic", source_provider=provider,
        source_credential=credential, source_collection=collection,
    )


def capability(**overrides):
    values = dict(
        operation="loan.borrow", availability="supported",
        reason_code="available_unverified_permission",
        manifest_declared=True, descriptor_declared=True,
        method_implemented=True, session_ready=True,
        explicit_action_required=True,
    )
    values.update(overrides)
    return LendingOperationFact(**values)


def proof(a=None, item=None, **overrides):
    a = a or account()
    item = item or notice()
    values = dict(
        identity_id=a.identity_id, provider_id=a.provider_id,
        credential_id=a.credential_id, borrower_id=a.unique_id,
        docbase=item.docbase, record_id=item.record_id,
        source_collection=item.source_collection, operation="loan.borrow",
        account_state="active", quota_state="available",
        item_offer="available", permission="allowed",
    )
    values.update(overrides)
    return BorrowerOperationEvidence(**values)


def decide(a=None, item=None, ev=None, cap=None):
    a = a or account()
    item = item or notice()
    return assess_borrower_eligibility(
        account=a, notice=item,
        capability=cap if cap is not None else capability(),
        evidence=ev,
    )


def test_two_paris_readers_have_declared_borrow_but_different_real_blockers():
    a = account(borrower="reader-A")
    b = account(borrower="reader-B")
    item = notice()
    # A has a spare slot, but subscription renewal is pending.
    evidence_a = proof(a, item, account_state="renewal_pending",
                       quota_state="available", permission="unknown")
    # B is subscribed, but the borrower-specific loan quota is exhausted.
    evidence_b = proof(b, item, account_state="active",
                       quota_state="exhausted", permission="unknown")
    outcome_a = decide(a, item, evidence_a)
    outcome_b = decide(b, item, evidence_b)
    assert outcome_a.status == outcome_b.status == "blocked"
    assert outcome_a.reason_code == "subscription_renewal_pending"
    assert outcome_b.reason_code == "borrower_quota_exhausted"
    assert outcome_a.capability_supported and outcome_b.capability_supported
    assert outcome_a.account_ready and outcome_b.account_ready
    assert not outcome_a.status == "eligible" and not outcome_b.status == "eligible"
    # A's renewal and B's quota cannot be projected onto the other borrower.
    assert decide(a, item, evidence_b).reason_code == "evidence_scope_mismatch"
    assert decide(b, item, evidence_a).reason_code == "evidence_scope_mismatch"


@pytest.mark.parametrize("change", [
    {"identity_id": "bob"}, {"provider_id": "bnf-pret"},
    {"credential_id": "card-B"}, {"borrower_id": "reader-B"},
    {"docbase": "SYRACUSE"}, {"record_id": "paper-edition"},
    {"source_collection": "foreign-collection"},
    {"operation": "loan.return"},
])
def test_unrelated_account_branch_edition_or_operation_cannot_supply_evidence(change):
    outcome = decide(ev=proof(**change))
    assert outcome.status == "unverified"
    assert outcome.reason_code == "evidence_scope_mismatch"
    assert not outcome.blocked


@pytest.mark.parametrize("item_change", [
    {"credential": "card-B"}, {"provider": "bnf-pret"},
    {"docbase": "SYRACUSE"}, {"record": "paper-edition"},
    {"collection": "another-collection"},
])
def test_two_notices_same_title_cannot_share_borrow_permission(item_change):
    item = notice(**item_change)
    outcome = decide(item=item, ev=proof())
    assert outcome.status == "unverified"
    assert outcome.reason_code in {"scope_unverified", "evidence_scope_mismatch"}


@pytest.mark.parametrize(("changed", "expected"), [
    ({"account_state": "renewal_pending"}, "subscription_renewal_pending"),
    ({"account_state": "inactive"}, "subscription_inactive"),
    ({"quota_state": "exhausted"}, "borrower_quota_exhausted"),
    ({"permission": "denied"}, "borrower_permission_denied"),
    ({"item_offer": "unavailable"}, "item_offer_unavailable"),
])
def test_only_attested_negative_explains_a_blocker(changed, expected):
    event = proof(**changed)
    outcome = decide(ev=event)
    assert outcome.status == "blocked" and outcome.reason_code == expected


@pytest.mark.parametrize(("changed", "expected"), [
    ({"account_state": "unknown"}, "subscription_unverified"),
    ({"quota_state": "unknown"}, "quota_unverified"),
    ({"permission": "unknown"}, "permission_unverified"),
    ({"item_offer": "unknown"}, "item_offer_unverified"),
])
def test_missing_field_does_not_invent_authorization_or_a_denial(changed, expected):
    outcome = decide(ev=proof(**changed))
    assert outcome.status == "unverified" and outcome.reason_code == expected


def test_catalogue_canloan_or_generic_status_does_not_attest_quota():
    item = notice()
    item.metadata["ItemHoldingsData"] = {"CanLoan": True}
    item.metadata["account_status"] = {"usable": True}
    # A real provider method and visible stock say nothing about quota
    # or renewal/operation permission; neither is implicitly consulted.
    result = decide(item=item)
    assert result.status == "unverified"
    assert result.reason_code == "evidence_missing"
    assert result.capability_supported is True


@pytest.mark.parametrize(("cap", "expected"), [
    (capability(availability="unsupported"), "capability_unavailable"),
    (capability(availability="unknown"), "capability_unavailable"),
    (capability(manifest_declared=False), "capability_unavailable"),
    (capability(session_ready=False), "session_unavailable"),
    (capability(operation="loan.list"), "capability_unavailable"),
])
def test_unavailable_capability_precedes_eligibility(cap, expected):
    result = decide(ev=proof(), cap=cap)
    assert result.status == "unavailable" and result.reason_code == expected


def test_fully_attested_positive_is_still_advisory_not_an_action():
    result = decide(ev=proof())
    assert result.status == "eligible"
    assert result.reason_code == "borrower_eligible_unconfirmed"
    # No method on this fact executes a borrow or authorizes consent:
    assert not hasattr(result, "borrow") and not hasattr(result, "admitted")


@pytest.mark.parametrize(("field", "bad"), [
    ("quota_state", -1), ("account_state", ["active"]),
    ("permission", True), ("item_offer", "CanLoan=true"),
])
def test_invalid_or_untyped_provider_values_fail_closed(field, bad):
    outcome = decide(ev=replace(proof(), **{field: bad}))
    assert outcome.status == "unverified"
    assert outcome.reason_code == "evidence_invalid"


def test_legacy_primary_account_empty_id_must_be_explicit_not_wildcard():
    a = account(borrower="")
    event = proof(a, notice())
    assert decide(a, ev=event).status == "eligible"
    assert decide(a, ev=replace(event, borrower_id="primary")).reason_code == (
        "evidence_scope_mismatch"
    )


def test_incomplete_edition_and_wrong_medium_do_not_reuse_account_permission():
    a = account()
    item = notice()
    assert decide(item=replace(item, docbase=""), ev=proof()).reason_code == (
        "scope_unverified"
    )
    assert decide(item=replace(item, medium="physical"), ev=proof()).reason_code == (
        "scope_unverified"
    )


def test_evidence_is_an_immutable_snapshot():
    with pytest.raises((AttributeError, TypeError)):
        proof().quota_state = "exhausted"
