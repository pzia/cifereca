"""P1 #4988 — normal lender attempt without fabricated positive evidence.

An explicit click may attempt the same claimed provider borrow for A while B
remains selected. The synthetic supplier is exercised without network I/O,
delivery, copy or profile switching; the claim is the actual SQLite one.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from cifereca.core.borrow_action_scope import borrow_claim_key
from cifereca.core.borrow_for_identity_discovery import (
    discover_possible_borrow_lenders,
)
from cifereca.core.borrow_for_identity_normal_executor import (
    execute_normal_lender_borrow,
)
from tests.test_core_p1_4987_cross_profile_executor import BorrowingOffline
from tests.test_core_p1_4662_live_lender_preview import _candidate, harness


def setup(harness, outcome="confirmed"):
    service, account, _ = harness
    session = BorrowingOffline(account, service, outcome)
    service._sessions[service._session_key(
        account.provider_id, account.credential_id,
    )] = session
    item = _candidate().notice
    requester = service.current_identity
    account_epoch = service._account_context_epoch
    source_epoch = service._source_context_epoch
    scope = lambda: (
        service.current_identity is requester
        and service._account_context_epoch == account_epoch
        and service._source_context_epoch == source_epoch
    )
    options = discover_possible_borrow_lenders(
        service, requesting_identity_id="default", notices=(item,),
    )
    assert len(options) == 1
    return service, account, session, item, options[0], scope


def execute(service, option, item, scope, *, confirmed=True):
    return execute_normal_lender_borrow(
        service, captured_candidate=option, exact_notice=item,
        requesting_identity_id="default", scope_current=scope,
        confirmed=confirmed,
    )


def test_normal_lender_borrow_uses_one_claim_without_positive_evidence(harness):
    service, account, session, item, option, scope = setup(harness)
    requester = service.current_identity
    assert option.executable is False
    assert option.supplier_eligibility == "not_verified"
    assert execute(service, option, item, scope)["status"] == "confirmed"
    assert session.actions == 1 and session.callbacks == [True]
    assert service.current_identity is requester
    receipt = service.storage.loan_borrow_state(
        borrow_claim_key(account, item),
    )
    assert receipt["status"] == "confirmed"
    assert receipt["verified_loan_id"] == "loan-synthetic"
    assert execute(service, option, item, scope)["status"] == "blocked"
    assert session.actions == 1
    assert service.cross_profile_borrow_progress(
        requesting_identity_id="default", borrowing_account=account,
        exact_notice=item,
    ).loan == "confirmed"


def test_confirmed_flag_is_necessary_and_does_not_grant_a_permission(harness):
    service, account, session, item, option, scope = setup(harness)
    result = execute(service, option, item, scope, confirmed=False)
    assert result["status"] == "blocked"
    assert result["reason_code"] == "borrow_confirmation_required"
    assert service.storage.loan_borrow_state(
        borrow_claim_key(account, item),
    ) is None
    assert session.actions == 0


@pytest.mark.parametrize("outcome", [
    "unknown", "wrong_lender", "rebind", "inactive", "state:pending",
    "state:returned", "state:refused", "state:unknown",
])
def test_supplier_error_or_unverified_loan_consumes_claim_not_a_second_post(
    harness, outcome,
):
    service, account, session, item, option, scope = setup(harness, outcome)
    result = execute(service, option, item, scope)
    assert result["status"] == "verification_unavailable"
    assert result["retry_borrow"] is False
    assert result["delivery"] == "unavailable"
    assert result["transfer"] == "blocked"
    assert service.storage.loan_borrow_state(
        borrow_claim_key(account, item),
    )["status"] == "verification_unavailable"
    assert execute(service, option, item, scope)["status"] == "blocked"
    assert session.actions == 1


def test_explicit_provider_unsent_receipt_releases_only_unsent_claim(harness):
    service, account, session, item, option, scope = setup(harness, "unsent")
    result = execute(service, option, item, scope)
    assert result["status"] == "blocked"
    assert result["reason_code"] == "borrow_not_submitted"
    assert result["retry_borrow"] is False
    assert service.storage.metadata_get(borrow_claim_key(account, item)) is None
    assert session.actions == 1


@pytest.mark.parametrize("revoke", [
    "requester", "source_epoch", "account_epoch", "disabled_connection",
    "plugin_disabled", "session_rebind", "edition_changed",
])
def test_stale_or_rebound_choice_does_not_spend_claim_or_send_provider_post(
    harness, revoke,
):
    service, account, session, item, option, scope = setup(harness)
    original_key = borrow_claim_key(account, item)
    if revoke == "requester":
        service.current_identity = replace(service.current_identity)
    elif revoke == "source_epoch":
        service._source_context_epoch += 1
    elif revoke == "account_epoch":
        service._account_context_epoch += 1
    elif revoke == "disabled_connection":
        service.storage.provider_connection_put({
            "identity_id": "alex", "provider_id": "paris",
            "credential_id": "card-alex", "label": "Disabled",
            "enabled": False, "metadata": {},
        })
    elif revoke == "plugin_disabled":
        original = service._plugin_manager.status
        service._plugin_manager.status = lambda plugin_id: replace_dummy(
            original(plugin_id), enabled=False,
        )
    elif revoke == "session_rebind":
        service._sessions[service._session_key(
            account.provider_id, account.credential_id,
        )] = object()
    else:
        item.record_id = "another-edition"
    result = execute(service, option, item, scope)
    assert result["status"] == "blocked"
    assert result["retry_borrow"] is False
    assert session.actions == 0
    assert service.storage.loan_borrow_state(original_key) is None


def replace_dummy(value, **changes):
    from types import SimpleNamespace as NS
    return NS(**{**vars(value), **changes})


def test_atomic_claim_rebind_prevents_supplier_dispatch_and_spends_claim(
    harness, monkeypatch,
):
    service, account, session, item, option, scope = setup(harness)
    original = service.storage.loan_borrow_claim_once

    def after_claim(key, op):
        receipt = original(key, op)
        service._source_context_epoch += 1
        return receipt

    monkeypatch.setattr(service.storage, "loan_borrow_claim_once", after_claim)
    result = execute(service, option, item, scope)
    assert result["status"] == "blocked"
    assert session.actions == 0
    assert service.storage.loan_borrow_state(
        borrow_claim_key(account, item),
    )["status"] == "blocked"


def test_loan_edition_changed_by_provider_is_not_a_positive_receipt(
    harness, monkeypatch,
):
    service, account, session, item, option, scope = setup(harness)
    actual = session.borrow_explicit_with_claim

    def source_changes_item(*args, **kwargs):
        loan = actual(*args, **kwargs)
        item.record_id = "rebound-during-provider"
        return loan

    monkeypatch.setattr(session, "borrow_explicit_with_claim", source_changes_item)
    result = execute(service, option, item, scope)
    assert result["status"] == "verification_unavailable"
    assert session.actions == 1
    assert result["retry_borrow"] is False


def test_existing_confirmed_loan_never_uses_discovery_as_retry(harness):
    service, account, session, item, option, scope = setup(harness)
    assert execute(service, option, item, scope)["status"] == "confirmed"
    assert discover_possible_borrow_lenders(
        service, requesting_identity_id="default", notices=(item,),
    ) == ()
    assert session.actions == 1


def facade_execute(service, option, item, *, confirmed=True, requester=None,
                   identity_id="default", context_epoch=None, source_epoch=None):
    return service.execute_normal_lender_borrow_for_current_identity(
        captured_candidate=option, exact_notice=item,
        expected_requesting_identity_id=identity_id,
        expected_requesting_identity=(
            service.current_identity if requester is None else requester
        ),
        expected_context_epoch=(
            service._account_context_epoch if context_epoch is None
            else context_epoch
        ),
        expected_source_epoch=(
            service._source_context_epoch if source_epoch is None
            else source_epoch
        ),
        confirmed=confirmed,
    )


def test_facade_confirmed_normal_lender_uses_exact_captured_requester(harness):
    service, account, session, item, option, _scope = setup(harness)
    original = service.current_identity
    result = facade_execute(
        service, option, item, requester=original,
    )
    assert result["status"] == "confirmed"
    assert result["provider_verified"] is True
    assert result["transfer"] == "blocked"
    assert service.current_identity is original
    assert session.actions == 1
    assert service.storage.loan_borrow_state(
        borrow_claim_key(account, item),
    )["status"] == "confirmed"


@pytest.mark.parametrize("invalid", [
    "wrong_id", "wrong_identity_object", "account_epoch", "source_epoch",
    "no_confirmation", "replacement_identity", "wrong_notice",
])
def test_facade_refuses_stale_requester_and_mutation_without_spending_claim(
    harness, invalid,
):
    service, account, session, item, option, _scope = setup(harness)
    captured_identity = service.current_identity
    context_epoch = service._account_context_epoch
    source_epoch = service._source_context_epoch
    if invalid == "replacement_identity":
        service.current_identity = replace(captured_identity)
    result = service.execute_normal_lender_borrow_for_current_identity(
        captured_candidate=option,
        exact_notice=replace(item) if invalid == "wrong_notice" else item,
        expected_requesting_identity_id=(
            "foreign" if invalid == "wrong_id" else "default"
        ),
        expected_requesting_identity=(
            replace(captured_identity) if invalid == "wrong_identity_object"
            else captured_identity
        ),
        expected_context_epoch=(
            context_epoch + 1 if invalid == "account_epoch"
            else context_epoch
        ),
        expected_source_epoch=(
            source_epoch + 1 if invalid == "source_epoch"
            else source_epoch
        ),
        confirmed=invalid != "no_confirmation",
    )
    assert result["status"] == "blocked"
    assert result["retry_borrow"] is False
    assert service.storage.loan_borrow_state(borrow_claim_key(account, item)) is None
    assert session.actions == 0


def test_facade_does_not_rebind_to_different_profile_with_same_edition(harness):
    service, account, session, item, option, _scope = setup(harness)
    original = service.current_identity
    source_epoch = service._source_context_epoch
    context_epoch = service._account_context_epoch
    service._account_context_epoch += 1
    service._account_context_epoch += 1
    result = service.execute_normal_lender_borrow_for_current_identity(
        captured_candidate=option, exact_notice=item,
        expected_requesting_identity_id="default",
        expected_requesting_identity=original,
        expected_context_epoch=context_epoch,
        expected_source_epoch=source_epoch,
        confirmed=True,
    )
    assert result["status"] == "blocked"
    assert session.actions == 0



@pytest.mark.parametrize("declared,observed,expected", [
    (None, "", "confirmed"),
    ("primary", "primary", "confirmed"),
    ("main_reader", "main_reader", "confirmed"),
    (None, "primary", "verification_unavailable"),
    ("main_reader", "primary", "verification_unavailable"),
    ("primary", "other_reader", "verification_unavailable"),
])
def test_normal_lender_confirmation_uses_declared_observed_borrower_alias(
    harness, declared, observed, expected,
):
    service, account, session, item, option, scope = setup(
        harness, "borrower:" + observed,
    )
    session.descriptor = replace(
        session.descriptor, existing_primary_loan_account_alias=declared,
    )
    result = execute(service, option, item, scope)
    assert result["status"] == expected
    assert result["retry_borrow"] is False
    assert session.actions == 1
    assert service.storage.loan_borrow_state(
        borrow_claim_key(account, item),
    )["status"] == expected
    assert execute(service, option, item, scope)["status"] == "blocked"
    assert session.actions == 1


def test_direct_normal_executor_uses_explicit_requester_without_navigation(harness):
    service, account, session, item, option, _scope = setup(harness)

    class Headless:
        def __getattr__(self, name):
            if name == "current_identity":
                pytest.fail("Core worker read navigation")
            return getattr(service, name)

    result = execute(Headless(), option, item, lambda: True)
    assert result["status"] == "confirmed"
    assert session.actions == 1
    assert service.storage.loan_borrow_state(
        borrow_claim_key(account, item),
    )["status"] == "confirmed"
    assert execute(Headless(), option, item, lambda: True)["status"] == "blocked"
    assert session.actions == 1


@pytest.mark.parametrize("change", [
    {"revision_id": "new-revision"},
    {"scenario": "CATALOGUE"},
    {"source_account_id": ""},
    {"source_account_id": "other-borrower"},
])
def test_extra_notice_coordinates_are_revalidated_before_claim(harness, change):
    service, account, session, item, option, scope = setup(harness)
    original_key = borrow_claim_key(account, item)
    for key, value in change.items():
        setattr(item, key, value)
    assert execute(service, option, item, scope)["status"] == "blocked"
    assert borrow_claim_key(account, item) == original_key
    assert service.storage.loan_borrow_state(original_key) is None
    assert session.actions == 0


def test_revision_changed_after_claim_never_dispatches(harness, monkeypatch):
    service, account, session, item, option, scope = setup(harness)
    original_key = borrow_claim_key(account, item)
    original = service.storage.loan_borrow_claim_once

    def after_claim(key, op):
        receipt = original(key, op)
        item.revision_id = "new-revision"
        return receipt

    monkeypatch.setattr(service.storage, "loan_borrow_claim_once", after_claim)
    assert execute(service, option, item, scope)["status"] == "blocked"
    assert session.actions == 0
    assert service.storage.loan_borrow_state(original_key)["status"] == "blocked"


@pytest.mark.parametrize("change", ["replacement", "generation", "generation_type"])
def test_old_lender_consent_cannot_rebind_same_account_to_new_session(harness, change):
    service, account, session, item, option, scope = setup(harness)
    key = borrow_claim_key(account, item)
    replacement = session
    if change == "replacement":
        replacement = BorrowingOffline(account, service)
        service._sessions[service._session_key(
            account.provider_id, account.credential_id,
        )] = replacement
    elif change == "generation":
        session._epoch += 1
    else:
        session._epoch = str(session._epoch)
    # Deliberately do not rotate the service epochs. Both sessions have
    # the same legitimate Account object and the same native notice.
    assert scope()
    assert execute(service, option, item, scope)["status"] == "blocked"
    assert service.storage.loan_borrow_state(key) is None
    assert session.actions == replacement.actions == 0
    fresh, = discover_possible_borrow_lenders(
        service, requesting_identity_id="default", notices=(item,),
    )
    assert execute(service, fresh, item, scope)["status"] == "confirmed"
    assert replacement.actions == 1
    assert service.storage.loan_borrow_state(key)["status"] == "confirmed"


def test_lender_generation_change_during_claim_prevents_dispatch(harness, monkeypatch):
    service, account, session, item, option, scope = setup(harness)
    key = borrow_claim_key(account, item)
    original = service.storage.loan_borrow_claim_once

    def after_claim(key, op):
        receipt = original(key, op)
        session._epoch += 1
        return receipt

    monkeypatch.setattr(service.storage, "loan_borrow_claim_once", after_claim)
    assert execute(service, option, item, scope)["status"] == "blocked"
    assert session.actions == 0
    assert service.storage.loan_borrow_state(key)["status"] == "blocked"


@pytest.mark.parametrize("variant", ["single", "duplicate", "empty", "foreign_notice", "missing_selected"])
def test_facade_checks_entire_original_lender_roster(harness, variant):
    service, account, session, item, option, _scope = setup(harness)
    roster = (option,)
    if variant == "duplicate":
        roster = (option, option)
    elif variant == "empty":
        roster = ()
    elif variant == "foreign_notice":
        other = replace(item, record_id="unrelated-edition")
        alternative, = discover_possible_borrow_lenders(
            service, requesting_identity_id="default", notices=(other,),
        )
        roster = (option, alternative)
    elif variant == "missing_selected":
        roster = (replace(option),)
    result = service.execute_normal_lender_borrow_for_current_identity(
        captured_candidate=option, exact_notice=item, captured_roster=roster,
        expected_requesting_identity_id="default",
        expected_requesting_identity=service.current_identity,
        expected_context_epoch=service._account_context_epoch,
        expected_source_epoch=service._source_context_epoch,
        confirmed=True,
    )
    key = borrow_claim_key(account, item)
    if variant == "single":
        assert result["status"] == "confirmed"
        assert session.actions == 1
        assert service.storage.loan_borrow_state(key)["status"] == "confirmed"
    else:
        assert result["status"] == "blocked"
        assert session.actions == 0
        assert service.storage.loan_borrow_state(key) is None
