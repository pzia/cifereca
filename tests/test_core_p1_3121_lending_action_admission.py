"""P1 #3121: manual action admission needs capability, exact scope and consent.

All examples are synthetic. No provider session, network, download, mutation,
staging, entitlement update or ADEPT call is made by this API.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from cifereca.models import Account, Loan, SearchResult
from cifereca.providers.base import ProviderCapabilities, ProviderDescriptor
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


def declared_descriptor(provider: str, *, alias: str | None = None):
    return ProviderDescriptor(
        id=provider, display_name="Synthetic provider", description="test",
        state="ready", capabilities=ProviderCapabilities(),
        existing_primary_loan_account_alias=alias,
    )


def decide(op, a, target, **opts):
    # Paris's historical primary alias is explicit in its installed
    # descriptor, not a privileged provider branch in the admission engine.
    descriptor = opts.pop("descriptor", None)
    if descriptor is None and a.provider_id == "paris":
        descriptor = declared_descriptor("paris", alias="primary")
    return assess_lending_action(
        op, fact=fact(op), account=a, target=target,
        descriptor=descriptor, **opts,
    )


def test_manual_renew_requires_exact_confirmation_and_separate_permission():
    a = account()
    item = loan(a)
    assert decide(
        "loan.renew", a, item, confirmed=True, permission_attested=True,
    ).admitted
    for confirmation in (False, None, 1, "yes"):
        refused = decide(
            "loan.renew", a, item, confirmed=confirmation, permission_attested=True,
        )
        assert not refused.admitted
        assert refused.reason_code == "confirmation_required"
    no_rights = decide("loan.renew", a, item, confirmed=True)
    assert not no_rights.admitted and no_rights.reason_code == "permission_unverified"
    # A Loan.can_return flag or signed download URL is not permission to renew.
    assert item.can_return and item.ebook_url and no_rights.status == "blocked"


@pytest.mark.parametrize("foreign", [
    {"credential": "foreign"}, {"provider": "bnf-pret"},
    {"identity": "bob"}, {"borrower": "linked"},
])
def test_wrong_scope_never_inherits_consent_or_entitlement(foreign):
    a = account()
    other = account(**foreign)
    denied = decide(
        "loan.renew", a, loan(other),
        confirmed=True, permission_attested=True,
    )
    assert denied.reason_code == "scope_unverified" and not denied.admitted


def test_paris_primary_alias_is_exact_not_default_for_other_loan_sources():
    a = account()
    assert decide(
        "loan.renew", a, loan(a, borrower="primary"),
        confirmed=True, permission_attested=True,
    ).admitted
    linked = account(borrower="reader-two")
    denied = decide(
        "loan.renew", linked, loan(linked, borrower="primary"),
        confirmed=True, permission_attested=True,
    )
    assert denied.reason_code == "scope_unverified"
    bnf = account(provider="bnf-pret", borrower="reader")
    assert decide(
        "loan.renew", bnf, loan(bnf, borrower="primary"),
        confirmed=True, permission_attested=True,
    ).reason_code == "scope_unverified"


def test_primary_alias_requires_exact_descriptor_not_provider_name():
    a = account(provider="synthetic-provider")
    item = loan(a, borrower="main")
    positive = dict(confirmed=True, permission_attested=True)
    assert not assess_lending_action(
        "loan.renew", fact=fact("loan.renew"), account=a,
        target=item, **positive,
    ).admitted
    assert decide(
        "loan.renew", a, item,
        descriptor=declared_descriptor("synthetic-provider", alias="main"),
        **positive,
    ).admitted
    for descriptor in (
        declared_descriptor("synthetic-provider", alias="other"),
        declared_descriptor("unrelated-provider", alias="main"),
        declared_descriptor("synthetic-provider"),
    ):
        assert decide(
            "loan.renew", a, item, descriptor=descriptor, **positive,
        ).reason_code == "scope_unverified"
    linked = account(provider="synthetic-provider", borrower="linked")
    assert decide(
        "loan.renew", linked, loan(linked, borrower="main"),
        descriptor=declared_descriptor("synthetic-provider", alias="main"),
        **positive,
    ).reason_code == "scope_unverified"


def test_paris_alias_is_no_longer_implicit_without_declaration():
    a = account()
    assert assess_lending_action(
        "loan.renew", fact=fact("loan.renew"), account=a,
        target=loan(a, borrower="primary"),
        confirmed=True, permission_attested=True,
    ).reason_code == "scope_unverified"


@pytest.mark.parametrize("op", ["loan.renew", "loan.download_token"])
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
    if op == "loan.download_token":
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
        fact("loan.renew", status="unsupported"),
        fact("loan.renew", status="unknown"),
        fact("loan.renew", declared=False),
        fact("loan.renew", ready=False),
        None,
    ):
        result = assess_lending_action(
            "loan.renew", fact=absent, account=a, target=loan(a),
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
    # Even opt-in scheduling must not turn inventory into a borrow or renewal.
    for operation in ("loan.borrow", "loan.renew"):
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
        "loan.renew", a, row, origin="cron", confirmed=True,
        permission_attested=True,
    )
    assert not denied.admitted
    assert denied.reason_code == "origin_not_permitted"
    assert not decide(
        "loan.acquire", a, row, confirmed=True, permission_attested=True,
    ).admitted
    cancelled = decide(
        "loan.renew", a, row, confirmed=True,
        permission_attested=True, cancelled=True,
    )
    assert cancelled.status == "cancelled"
    # cancelled=1 is not a native cancellation token, nor is it consent.
    assert not decide(
        "loan.renew", a, row, confirmed=1,
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
        "loan.renew", "manual", "submitted",
        evidence="provider_accepted", reason_code="provider_submitted",
    )
    assert sent.contract == LENDING_ACTION_CONTRACT
    assert sent.status == "submitted"
    confirmed = LendingActionOutcome(
        "loan.renew", "manual", "confirmed",
        evidence="provider_verified", reason_code="provider_confirmed",
    )
    assert confirmed.status == "confirmed"
    for status, evidence, operation in (
        ("confirmed", "provider_accepted", "loan.renew"),
        ("confirmed", "none", "loan.renew"),
        ("confirmed", "provider_verified", "loan.download_token"),
        ("submitted", "none", "loan.borrow"),
        ("submitted", "provider_accepted", "loan.download_token"),
        ("verification_unavailable", "provider_verified", "loan.renew"),
        ("failed", "provider_verified", "loan.renew"),
    ):
        with pytest.raises(ValueError):
            LendingActionOutcome(
                operation, "manual", status, evidence=evidence,
                reason_code="unknown",
            )
    pending = LendingActionOutcome(
        "loan.renew", "manual", "verification_unavailable",
        evidence="provider_accepted", reason_code="provider_confirmation_unavailable",
    )
    assert pending.status != "confirmed"
    with pytest.raises(ValueError):
        LendingActionOutcome(
            "loan.renew", "manual", "confirmed",
            evidence="provider_verified", reason_code="PRIVATE signed URL",
        )


def test_malformed_account_and_loan_fields_fail_without_coercion():
    a = account()
    row = loan(a)
    hostile = object()
    for bad in (
        replace(row, source_account_id=hostile),
        replace(row, source_provider=hostile),
        replace(row, medium=hostile),
        replace(row, id="bad" + chr(0) + "id"),
        replace(row, record_id=""),
    ):
        result = decide(
            "loan.renew", a, bad, confirmed=True, permission_attested=True,
        )
        assert result.status == "blocked"
        assert result.reason_code == "scope_unverified"


@pytest.mark.parametrize("permission", [False, True])
@pytest.mark.parametrize("confirmed", [False, True])
@pytest.mark.parametrize("origin", ["manual", "opening", "scheduled"])
def test_retired_loan_return_is_never_admissible_or_valid_outcome(
    permission, confirmed, origin,
):
    """#4048: even full positive rights and opt-in cannot restore loan.return."""
    a = account()
    result = decide(
        "loan.return", a, loan(a),
        confirmed=confirmed, permission_attested=permission, origin=origin,
        policy=LendingActionOriginPolicy(opening=True, scheduled=True),
    )
    assert result.operation == "unknown"
    assert result.status == "unsupported"
    assert result.reason_code == "operation_unavailable"
    assert not result.admitted
    with pytest.raises(ValueError, match="invalid_loan_action_outcome"):
        LendingActionOutcome(
            "loan.return", "manual", "submitted",
            evidence="provider_accepted", reason_code="provider_submitted",
        )
