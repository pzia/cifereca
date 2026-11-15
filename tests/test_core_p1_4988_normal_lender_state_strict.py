"""P1 #4988 — a supplier's Loan.state is positive proof only when active.

The normal lender path never infers activation from a non-closed value. A
malformed state can arrive in a mutable Loan even if its declared annotation
is str; it must spend the ambiguous one-shot claim without a second POST.
"""
from __future__ import annotations

import pytest

from cifereca.core.borrow_action_scope import borrow_claim_key
from tests.test_core_p1_4988_normal_lender_borrow import setup, execute
from tests.test_core_p1_4662_live_lender_preview import harness


class _UnknownState:
    def __repr__(self):
        raise AssertionError("opaque provider state must not be logged")


@pytest.mark.parametrize("bad_state", [
    None, False, True, 0, 1, [], {}, (), _UnknownState(),
    "", " ", "ACTIVE", " active ", "active\n", "issued",
    "loaned", "borrowed", "pending", "unknown", "expired",
    "cancelled", "returned",
], ids=[
    "none", "false", "true", "zero", "one", "list", "dict",
    "tuple", "opaque", "empty", "blank", "uppercase", "whitespace",
    "newline", "issued", "loaned", "borrowed", "pending", "unknown",
    "expired", "cancelled", "returned",
])
def test_unrecognized_or_malformed_supplier_state_cannot_confirm_loan(
    harness, monkeypatch, bad_state,
):
    service, account, session, item, option, scope = setup(harness)
    original = session.borrow_explicit_with_claim

    def malformed_state(*args, **kwargs):
        loan = original(*args, **kwargs)
        loan.state = bad_state
        return loan

    monkeypatch.setattr(session, "borrow_explicit_with_claim", malformed_state)
    result = execute(service, option, item, scope)
    assert result["status"] == "verification_unavailable"
    assert result["provider_verified"] is False
    assert result["retry_borrow"] is False
    assert result["delivery"] == "unavailable"
    assert result["transfer"] == "blocked"
    claim = service.storage.loan_borrow_state(borrow_claim_key(account, item))
    assert claim["status"] == "verification_unavailable"
    assert claim["request_accepted"] is False
    assert session.actions == 1

    # The invalid supplier response may conceal a real upstream mutation.
    # Neither the original candidate nor a new discovery can replay it.
    assert execute(service, option, item, scope)["status"] == "blocked"
    assert session.actions == 1


def test_exact_active_supplier_state_remains_the_only_positive_status(harness):
    service, account, session, item, option, scope = setup(harness)
    result = execute(service, option, item, scope)
    assert result["status"] == "confirmed"
    assert result["provider_verified"] is True
    assert result["retry_borrow"] is False
    assert result["transfer"] == "blocked"
    assert service.storage.loan_borrow_state(borrow_claim_key(account, item))[
        "status"
    ] == "confirmed"
    assert session.actions == 1
