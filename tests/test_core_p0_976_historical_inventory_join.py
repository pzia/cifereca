"""#976: join historical coordinates, never infer rights or act on them."""
from dataclasses import replace

import pytest

from cifereca.core import adept_historical_inventory_join as review
from tests.test_core_p0_3171_atomic_publication_v14 import store, register, new_source


def _setup(store):
    source, _snapshot = new_source(kind="loan", branch="loans", right="loan-1")
    register(store, "A", "seller", "card-A")
    row = {
        "acquisition_id": "historical", "identity_id": "A", "provider_id": "seller",
        "credential_id": "card-A", "account_id": "buyer-A", "item_id": "edition",
        "status": "complete", "strategy": "loan_auto", "executor": "kobo-adept",
        "metadata": {"provider_loan_id": "loan-1", "adept_binding": {
            "adept_profile_id": "historical-adobe", "adept_loan_id": "adobe-loan",
            "adept_profile_name": "shared-name",
        }},
    }
    store.acquisition_put(row)
    return source, row


def _loan(**kwargs):
    return dict(id="adobe-loan", returned=False, expired=False,
                token_present=True, validity="2030-01-01", **{}) | kwargs


def _review(store, source, **kwargs):
    args = dict(inventory_profile_id="historical-adobe", inventory_complete=True,
                inventory_loans=[_loan()])
    args.update(kwargs)
    return review.review_legacy_adept_inventory(store, source=source, **args)


@pytest.mark.parametrize("flags,state", [
    ({}, "active_reported"),
    ({"expired": True}, "expired_reported"),
    ({"returned": True, "token_present": False}, "returned_reported"),
    ({"returned": True}, "contradictory_report"),
    ({"validity": None}, "state_unverified"),
])
def test_exact_profile_and_loan_only_corroborate_report(store, flags, state):
    source, row = _setup(store)
    before = store.acquisition_get("historical")
    result = _review(store, source, inventory_loans=[_loan(**flags)])
    assert result["recorded_binding"] == "recorded"
    assert result["inventory_comparison"] == "exact_profile_and_loan_reported"
    assert result["adept_license_state_reported"] == state
    assert result["provider_loan_state"] == result["canonical_placement_state"] == "not_checked"
    assert not result["activation_attested"] and not result["inventory_transport_attested"]
    assert not result["may_rebind_or_return"] and not result["may_reacquire"]
    assert "historical-adobe" not in repr(result)
    assert store.acquisition_get("historical") == before


@pytest.mark.parametrize("kwargs,expected", [
    ({"inventory_profile_id": "shared-name"}, "profile_mismatch"),
    ({"inventory_complete": False}, "inventory_incomplete"),
    ({"inventory_loans": [_loan(id="other")]}, "not_found_in_supplied_inventory"),
    ({"inventory_loans": [_loan(), _loan()]}, "ambiguous_loan"),
    ({"inventory_loans": [_loan(expired="false")]}, "inventory_unverified"),
])
def test_inexact_inventory_is_never_positive(store, kwargs, expected):
    source, _row = _setup(store)
    result = _review(store, source, **kwargs)
    assert result["inventory_comparison"] == expected
    assert result["adept_license_state_reported"] == "not_checked"


def test_duplicate_transaction_and_other_scope_do_not_match(store):
    source, row = _setup(store)
    other = replace(source, scope=replace(source.scope, account_id="other"))
    assert _review(store, other)["recorded_binding"] == "not_recorded"
    store.acquisition_put({**row, "acquisition_id": "duplicate"})
    assert _review(store, source)["recorded_binding"] == "ambiguous_transaction"


def test_incomplete_attempt_and_budget_remain_distinct(store, monkeypatch):
    source, row = _setup(store)
    store.acquisition_put({**row, "status": "retryable_error"})
    assert _review(store, source)["recorded_binding"] == "historical_attempt_or_incomplete"
    monkeypatch.setattr(review, "_MAX_LEDGER_ROWS", 0)
    assert _review(store, source)["recorded_binding"] == "budget_exceeded"


def test_duplicate_json_refuses_without_write(store):
    source, _row = _setup(store)
    raw = '{"provider_loan_id":"loan-1","provider_loan_id":"other"}'
    with store._lock, store._connect() as db:
        db.execute("UPDATE acquisition_ledger SET metadata_json=? WHERE acquisition_id='historical'", (raw,))
    assert _review(store, source)["recorded_binding"] == "ledger_unverified"
    with store._lock, store._connect() as db:
        assert db.execute("SELECT metadata_json FROM acquisition_ledger WHERE acquisition_id='historical'").fetchone() == (raw,)
