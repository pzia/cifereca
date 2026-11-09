"""P0 #5055: exact selected loan offer is provider-neutral and fails closed."""
from types import SimpleNamespace as NS

import pytest

from cifereca.core.selected_loan_offer import select_exact_loan_offer


def _rig(*, provider="future-provider", borrower="reader-a",
         branch="private-loans", delivery=True):
    trace = []
    account = NS(identity_id="alice", provider_id=provider,
                 credential_id="cred-a", unique_id=borrower)
    loan = NS(record_id="edition-a", id="loan-a", source_provider=provider)
    right = NS(provider_attested=True, delivery_attested=delivery,
               delivery_offer_id="opaque-offer")
    selected = NS(
        scope=NS(identity_id="alice", provider_id=provider,
                 credential_id="cred-a", account_id=borrower),
        branch=branch,
        exact=lambda *args: trace.append(("exact", *args)) or right,
    )
    def snapshot(*args, **kwargs):
        trace.append(("snapshot", kwargs))
        return selected
    def candidate(*args, **kwargs):
        trace.append(("GET",))
        raise AssertionError("selected offer check must never execute a GET")
    session = NS(rights_selected_delivery_offer_snapshot=snapshot,
                 rights_selected_delivery_candidate=candidate)
    return session, account, loan, selected, right, trace


@pytest.mark.parametrize("provider,branch", [
    ("future-provider", "private-loans"), ("bnf-pret", "bnf_html"),
])
def test_selected_scope_and_attested_loan_offer(provider, branch):
    session, account, loan, selected, right, trace = _rig(
        provider=provider, branch=branch,
    )
    status, facts = select_exact_loan_offer(
        session, account, loan, branch=branch, current=lambda: True,
    )
    assert status == "selected"
    assert facts[0] is selected and facts[1] is right
    assert callable(facts[2])
    assert all(event[0] != "GET" for event in trace)


@pytest.mark.parametrize("mutation", [
    "scope_identity", "scope_provider", "scope_credential", "scope_borrower",
    "branch", "right_missing", "provider_unattested",
    "delivery_unattested", "empty_offer", "revoked",
])
def test_foreign_or_missing_selected_right_never_grants_candidate(mutation):
    session, account, loan, selected, right, trace = _rig()
    current = lambda: mutation != "revoked"
    if mutation.startswith("scope_"):
        setattr(selected.scope, mutation.removeprefix("scope_"), "other")
    elif mutation == "branch":
        selected.branch = "different"
    elif mutation == "right_missing":
        selected.exact = lambda *_args: None
    elif mutation == "provider_unattested":
        right.provider_attested = False
    elif mutation == "delivery_unattested":
        right.delivery_attested = False
    elif mutation == "empty_offer":
        right.delivery_offer_id = ""
    status, facts = select_exact_loan_offer(
        session, account, loan, branch="private-loans", current=current,
    )
    assert status == "scope_unproven" and facts is None
    assert all(event[0] != "GET" for event in trace)


@pytest.mark.parametrize("missing", [
    "rights_selected_delivery_offer_snapshot",
    "rights_selected_delivery_candidate",
])
def test_incomplete_abi_refuses_before_any_snapshot(missing):
    session, account, loan, _selected, _right, trace = _rig()
    setattr(session, missing, None)
    status, facts = select_exact_loan_offer(
        session, account, loan, branch="private-loans", current=lambda: True,
    )
    assert (status, facts) == ("unavailable", None)
    assert trace == []
