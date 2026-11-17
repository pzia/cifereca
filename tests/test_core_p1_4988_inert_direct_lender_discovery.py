"""#4988: discover *possible* lenders without two invented permission facts.

All cases use the real in-memory Core/session/SQLite fixture and no provider I/O.
Candidates are inert; executor/POST integration is a distinct tranche.
"""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from cifereca.core.borrow_for_identity_discovery import (
    PossibleBorrowLender, discover_possible_borrow_lenders,
)
from cifereca.core.borrow_action_scope import borrow_claim_key
from cifereca.models import Account
from tests.test_core_p1_4662_live_lender_preview import (
    ParisOffline, _candidate, harness,
)


def _discover(service, *notices, requester="default"):
    return discover_possible_borrow_lenders(
        service, requesting_identity_id=requester, notices=tuple(notices),
    )


def test_normal_borrow_lender_visible_without_prior_positive_eligibility(harness):
    service, account, session = harness
    # The only input is the already-loaded exact search notice, not a
    # fabricated BorrowerOperationEvidence or edition permission.
    notice = _candidate().notice
    before = service._account_context_epoch, service._source_context_epoch
    choices = _discover(service, notice)
    assert len(choices) == 1
    candidate, = choices
    assert type(candidate) is PossibleBorrowLender
    assert candidate.lender_identity_id == "alex"
    assert candidate.label == "Alex"
    assert candidate.supplier_eligibility == "not_verified"
    assert candidate.executable is False
    assert candidate._account is account and candidate._notice is notice
    assert "card-alex" not in repr(candidate)
    assert "record-1" not in repr(candidate)
    assert session.calls == 0
    assert service.current_identity.identity_id == "default"
    assert (service._account_context_epoch, service._source_context_epoch) == before


@pytest.mark.parametrize("variant", [
    "no_notices", "source_missing", "wrong_requester",
    "disconnected", "inactive_plugin", "foreign_session",
    "disabled_binding", "claim_spent", "not_primary",
])
def test_nonqualifying_or_spent_lender_never_becomes_local_candidate(
    harness, variant,
):
    service, account, session = harness
    notice = _candidate().notice
    if variant == "no_notices":
        assert _discover(service) == ()
        return
    if variant == "source_missing":
        notice = replace(notice, source_credential="foreign")
    elif variant == "wrong_requester":
        assert _discover(service, notice, requester="alex") == ()
        return
    elif variant == "disconnected":
        session.connected = False
    elif variant == "inactive_plugin":
        old = service._plugin_manager.status
        service._plugin_manager.status = lambda pid: NS(
            **{**vars(old(pid)), "enabled": False}
        )
    elif variant == "foreign_session":
        service._sessions[service._session_key("paris", account.credential_id)] = object()
    elif variant == "disabled_binding":
        service.storage.provider_connection_put({
            "identity_id": "alex", "provider_id": "paris",
            "credential_id": "card-alex", "label": "Fictive",
            "enabled": False, "metadata": {},
        })
    elif variant == "claim_spent":
        key = borrow_claim_key(account, notice)
        assert key is not None
        claimed, _receipt = service.storage.loan_borrow_claim_once(
            key, "one-atomic-attempt",
        )
        assert claimed
    elif variant == "not_primary":
        account.is_primary = False
    assert _discover(service, notice) == ()
    assert session.calls == 0


@pytest.mark.parametrize("second_label", ["Second", "Réseau municipal"])
def test_two_distinct_lender_accounts_are_not_assumed_one_edition_or_one_right(
    harness, second_label,
):
    service, account, first = harness
    second_account = Account(
        identity_id="alex", provider_id="paris",
        credential_id="card-second", unique_id="",
        display_name=second_label, is_primary=True,
    )
    second = ParisOffline(second_account)
    service.accounts.append(second_account)
    service._sessions[service._session_key(
        "paris", second_account.credential_id,
    )] = second
    service.storage.provider_connection_put({
        "identity_id": "alex", "provider_id": "paris",
        "credential_id": "card-second", "label": "Fictive",
        "enabled": True, "metadata": {},
    })
    a = _candidate().notice
    b = _candidate("card-second").notice
    choices = _discover(service, a, b, a)
    assert len(choices) == 2
    assert [choice._notice.source_credential for choice in choices] == [
        "card-alex", "card-second",
    ]
    assert all(not choice.executable and
               choice.supplier_eligibility == "not_verified" for choice in choices)
    assert len({choice.label for choice in choices}) == 2
    assert all("card-alex" not in choice.label and
               "card-second" not in choice.label for choice in choices)
    if second_label == "Réseau municipal":
        assert "choix 1/2" in choices[0].label
        assert "choix 2/2" in choices[1].label
    assert first.calls == second.calls == 0
    assert service.current_identity.identity_id == "default"


def test_epoch_revoked_during_plugin_status_discards_entire_candidate_list(
    harness,
):
    service, _account, session = harness
    original = service._plugin_manager.status

    def status(plugin_id):
        service._account_context_epoch += 1
        return original(plugin_id)

    service._plugin_manager.status = status
    assert _discover(service, _candidate().notice) == ()
    assert session.calls == 0


def test_untrusted_account_does_not_authorize_a_new_borrow(harness):
    service, _account, session = harness
    notice = _candidate().notice
    choices = _discover(service, notice)
    assert len(choices) == 1
    # Discovery is purely a possible lender: no loan borrow checkpoint was
    # written, no right or request was granted, and the next explicit click
    # must revalidate and produce its own durable claim.
    assert choices[0].executable is False
    assert service.storage.loan_borrow_state(
        borrow_claim_key(choices[0]._account, notice),
    ) is None
    assert session.calls == 0


def test_possible_lender_dto_cannot_be_forged_into_an_authorization(harness):
    service, _account, session = harness
    candidate, = _discover(service, _candidate().notice)
    with pytest.raises(ValueError, match="invalid_possible_borrow_lender"):
        replace(candidate, executable=True)
    with pytest.raises(ValueError, match="invalid_possible_borrow_lender"):
        replace(candidate, supplier_eligibility="eligible")
    with pytest.raises(ValueError, match="invalid_possible_borrow_lender"):
        replace(candidate, lender_identity_id="default")
    assert session.calls == 0


def test_shared_credential_in_two_profiles_cannot_select_a_lender(harness):
    service, _account, session = harness
    service.accounts.append(Account(
        identity_id="default", provider_id="paris",
        credential_id="card-alex", unique_id="",
        display_name="Requesting profile", is_primary=True,
    ))
    assert _discover(service, _candidate().notice) == ()
    assert session.calls == 0


def test_explicit_requester_discovery_is_headless_and_rejects_unknown_identity(harness):
    service, _account, session = harness
    class Headless:
        def __getattr__(self, name):
            if name == "current_identity":
                pytest.fail("Core worker read navigation")
            return getattr(service, name)
    proxy = Headless()
    choices = _discover(proxy, _candidate().notice)
    assert len(choices) == 1 and choices[0].lender_identity_id == "alex"
    assert _discover(proxy, _candidate().notice, requester="missing") == ()
    assert session.calls == 0
