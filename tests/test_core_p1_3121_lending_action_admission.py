"""P1 #3121: manual action admission needs capability, exact scope and consent.

All examples are synthetic. No provider session, network, download, mutation,
staging, entitlement update or ADEPT call is made by this API.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from cifereca.models import Account, Loan, SearchResult
from cifereca.plugin_api import (
    LENDING_ACTION_CONTRACT, LendingActionOriginPolicy,
    LendingActionOutcome, assess_lending_action,
)
from cifereca.plugin_api.lending_capabilities import LendingOperationFact


def account(*, provider="paris", credential="card", borrower="", identity="alice"):
    return Account(
        provider_id=provider, credential_id=credential, unique_id=borrower,
        identity_id=identity, is_primary=not bool(borrower),
    )


def loan(a, *, uid="loan-one", borrower=None, state="active", medium="digital"):
    return Loan(
        id=uid, record_id="book-one", title="Synthetic book",
        provider="DILICOM", medium=medium, state=state,
        source_provider=a.provider_id, source_credential=a.credential_id,
        source_account_id=(("primary" if a.provider_id == "paris"
                            and not a.unique_id else a.unique_id)
                           if borrower is None else borrower),
        identity_id=a.identity_id, renewable=True, can_return=True,
        ebook_url="https://private.invalid/acsm?sig=SECRET",
    )


def fact(op, *, status="supported", declared=True, ready=True):
    return LendingOperationFact(
        operation=op, availability=status,
        reason_code="available_unverified_permission",
        manifest_declared=declared, descriptor_declared=True,
        method_implemented=True, session_ready=ready,
        explicit_action_required=True,
    )


def decide(op, a, target, **opts):
    return assess_lending_action(
        op, fact=fact(op), account=a, target=target, **opts,
    )


def test_manual_return_requires_exact_confirmation_and_separate_permission():
    a = account()
    item = loan(a)
    assert decide(
        "loan.return", a, item, confirmed=True, permission_attested=True,
    ).admitted
    for confirmation in (False, None, 1, "yes"):
        refused = decide(
            "loan.return", a, item, confirmed=confirmation, permission_attested=True,
        )
        assert not refused.admitted
        assert refused.reason_code == "confirmation_required"
    no_rights = decide("loan.return", a, item, confirmed=True)
    assert not no_rights.admitted and no_rights.reason_code == "permission_unverified"
    # A Loan.can_return flag or signed download URL is not permission.
    assert item.can_return and item.ebook_url and no_rights.status == "blocked"


@pytest.mark.parametrize("foreign", [
    {"credential": "foreign"}, {"provider": "bnf-pret"},
    {"identity": "bob"}, {"borrower": "linked"},
])
def test_wrong_scope_never_inherits_consent_or_entitlement(foreign):
    a = account()
    other = account(**foreign)
    denied = decide(
        "loan.return", a, loan(other),
        confirmed=True, permission_attested=True,
    )
    assert denied.reason_code == "scope_unverified" and not denied.admitted


def test_paris_primary_alias_is_exact_not_default_for_other_loan_sources():
    a = account()
    assert decide(
        "loan.return", a, loan(a, borrower="primary"),
        confirmed=True, permission_attested=True,
    ).admitted
    linked = account(borrower="reader-two")
    denied = decide(
        "loan.return", linked, loan(linked, borrower="primary"),
        confirmed=True, permission_attested=True,
    )
    assert denied.reason_code == "scope_unverified"
    bnf = account(provider="bnf-pret", borrower="reader")
    assert decide(
        "loan.return", bnf, loan(bnf, borrower="primary"),
        confirmed=True, permission_attested=True,
    ).reason_code == "scope_unverified"


@pytest.mark.parametrize("op", ["loan.return", "loan.renew", "loan.download_token"])
def test_existing_loan_actions_require_positive_active_loan(op):
    a = account()
    for candidate in (
        loan(a, state="returned"), loan(a, state=None),
        replace(loan(a), id=""), replace(loan(a), record_id=None),
        replace(loan(a), identity_id=None),
    ):
        assert not decide(
            op, a, candidate, confirmed=True, permission_attested=True,
        ).admitted
    if op in {"loan.return", "loan.download_token"}:
        assert not decide(
            op, a, loan(a, medium="physical"),
            confirmed=True, permission_attested=True,
        ).admitted


def test_borrow_is_not_a_loan_and_needs_exact_search_result_plus_real_permission():
    a = account()
    result = SearchResult(
        id="notice-1", record_id="edition-1", docbase="books",
        medium="digital", title="Title", author=None, type=None, format=None,
        publisher=None, published_at=None, description=None,
        thumbnail_url=None, friendly_url=None, scenario="book",
        source_provider="paris", source_credential="card",
    )
    allowed = decide(
        "loan.borrow", a, result,
        confirmed=True, permission_attested=True,
    )
    assert allowed.admitted
    assert not decide("loan.borrow", a, result, confirmed=True).admitted
    assert not decide(
        "loan.borrow", a, loan(a),
        confirmed=True, permission_attested=True,
    ).admitted
    assert not decide(
        "loan.borrow", a, replace(result, source_credential="other"),
        confirmed=True, permission_attested=True,
    ).admitted


def test_read_only_sync_does_not_consent_to_loan_action():
    a = account()
    for absent in (
        fact("loan.return", status="unsupported"),
        fact("loan.return", status="unknown"),
        fact("loan.return", declared=False),
        fact("loan.return", ready=False),
        None,
    ):
        result = assess_lending_action(
            "loan.return", fact=absent, account=a, target=loan(a),
            confirmed=True, permission_attested=True,
        )
        assert not result.admitted and result.status == "unsupported"


def test_all_origins_default_deny_automation_and_never_schedule_mutations():
    a = account()
    row = loan(a)
    for origin in ("opening", "scheduled"):
        no_optin = decide(
            "loan.download_token", a, row, origin=origin,
            confirmed=True, permission_attested=True,
        )
        assert no_optin.reason_code == "origin_not_permitted"
    opted = LendingActionOriginPolicy(opening=True, scheduled=True)
    assert decide(
        "loan.download_token", a, row, origin="opening",
        confirmed=True, permission_attested=True, policy=opted,
    ).admitted
    assert decide(
        "loan.download_token", a, row, origin="scheduled",
        confirmed=True, permission_attested=True, policy=opted,
    ).admitted
    # Even opt-in scheduling must not turn inventory into a borrow or return.
    for operation in ("loan.borrow", "loan.return", "loan.renew"):
        target = row if operation != "loan.borrow" else None
        assert not decide(
            operation, a, target, origin="opening", confirmed=True,
            permission_attested=True, policy=opted,
        ).admitted
    with pytest.raises(ValueError):
        LendingActionOriginPolicy(opening=1)


def test_invalid_origin_operation_and_cancelled_fail_closed():
    a = account()
    row = loan(a)
    denied = decide(
        "loan.return", a, row, origin="cron", confirmed=True,
        permission_attested=True,
    )
    assert not denied.admitted
    assert denied.reason_code == "origin_not_permitted"
    assert not decide(
        "loan.acquire", a, row, confirmed=True, permission_attested=True,
    ).admitted
    cancelled = decide(
        "loan.return", a, row, confirmed=True,
        permission_attested=True, cancelled=True,
    )
    assert cancelled.status == "cancelled"
    # cancelled=1 is not a native cancellation token, nor is it consent.
    assert not decide(
        "loan.return", a, row, confirmed=1,
        permission_attested=True, cancelled=1,
    ).admitted


def test_bnf_private_download_method_is_not_a_public_action_capability():
    a = account(provider="bnf-pret", borrower="reader")
    internal_method_only = fact("loan.download_token", status="unsupported", declared=False)
    result = assess_lending_action(
        "loan.download_token", fact=internal_method_only,
        account=a, target=loan(a), confirmed=True, permission_attested=True,
    )
    assert not result.admitted and result.status == "unsupported"


def test_http_accepted_is_submitted_only_provider_verification_can_confirm():
    sent = LendingActionOutcome(
        "loan.return", "manual", "submitted",
        evidence="provider_accepted", reason_code="provider_submitted",
    )
    assert sent.contract == LENDING_ACTION_CONTRACT
    assert sent.status == "submitted"
    confirmed = LendingActionOutcome(
        "loan.return", "manual", "confirmed",
        evidence="provider_verified", reason_code="provider_confirmed",
    )
    assert confirmed.status == "confirmed"
    for status, evidence, operation in (
        ("confirmed", "provider_accepted", "loan.return"),
        ("confirmed", "none", "loan.return"),
        ("confirmed", "provider_verified", "loan.download_token"),
        ("submitted", "none", "loan.borrow"),
        ("failed", "provider_verified", "loan.return"),
    ):
        with pytest.raises(ValueError):
            LendingActionOutcome(
                operation, "manual", status, evidence=evidence,
                reason_code="unknown",
            )
    pending = LendingActionOutcome(
        "loan.return", "manual", "verification_unavailable",
        evidence="provider_accepted", reason_code="provider_confirmation_unavailable",
    )
    assert pending.status != "confirmed"
    with pytest.raises(ValueError):
        LendingActionOutcome(
            "loan.return", "manual", "confirmed",
            evidence="provider_verified", reason_code="PRIVATE signed URL",
        )
