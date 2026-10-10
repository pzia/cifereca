"""Focused borrower-provenance safety regression tests (#976)."""
from types import SimpleNamespace

from cifereca.services import LibraryService


def account(identity, borrower, provider="paris"):
    return SimpleNamespace(identity_id=identity, unique_id=borrower, provider_id=provider)


def conflict(accounts, borrower="borrower-a", identity="alice"):
    return LibraryService._borrower_identity_conflict(
        accounts, provider_id="paris", account_id=borrower, identity_id=identity
    )


def test_single_borrower_bound_to_matching_identity():
    assert not conflict([account("alice", "borrower-a")])


def test_shared_linked_borrower_across_two_identities_is_blocked():
    rows = [account("alice", "borrower-a"), account("bob", "borrower-a")]
    assert conflict(rows, identity="alice")
    assert conflict(list(reversed(rows)), identity="bob")


def test_borrower_bound_to_different_identity_is_blocked():
    assert conflict([account("bob", "borrower-a")], identity="alice")


def test_separate_borrower_or_provider_does_not_create_false_conflict():
    assert not conflict([account("alice", "borrower-a"),
                         account("bob", "borrower-b"),
                         account("bob", "borrower-a", "kobo")])


def test_empty_borrower_identifier_is_blocked():
    assert conflict([account("alice", "borrower-a")], borrower="")


def test_missing_borrower_ownership_is_blocked():
    assert conflict([], borrower="borrower-a")


def test_unrelated_account_does_not_prove_borrower_identity():
    assert conflict([account("alice", "borrower-other")])


def transaction(identity, loan_id, *, strategy="loan_auto"):
    return {"identity_id": identity, "strategy": strategy,
            "metadata": {"provider_loan_id": loan_id}}


def test_same_loan_transaction_under_another_identity_is_blocked():
    rows = [transaction("alice", "loan-123")]
    assert LibraryService._cross_identity_loan_conflict(
        rows, provider_loan_id="loan-123", identity_id="bob"
    )


def test_same_loan_transaction_under_same_identity_is_allowed():
    rows = [transaction("alice", "loan-123")]
    assert not LibraryService._cross_identity_loan_conflict(
        rows, provider_loan_id="loan-123", identity_id="alice"
    )


def test_different_loan_does_not_block_new_acquisition():
    rows = [transaction("alice", "loan-other")]
    assert not LibraryService._cross_identity_loan_conflict(
        rows, provider_loan_id="loan-123", identity_id="bob"
    )


def test_unknown_loan_id_is_not_sufficient_for_adept():
    assert LibraryService._cross_identity_loan_conflict(
        [], provider_loan_id="", identity_id="alice"
    )


def test_automatic_acquisitions_are_serialized_in_process(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Lock
    from time import sleep

    service = object.__new__(LibraryService)
    counters = {"active": 0, "peak": 0}
    monitor = Lock()

    def guarded(self, loan, *, force_refresh=False):
        with monitor:
            counters["active"] += 1
            counters["peak"] = max(counters["peak"], counters["active"])
        sleep(0.02)
        with monitor:
            counters["active"] -= 1
        return {"status": "unchanged"}

    monkeypatch.setattr(LibraryService, "_acquire_active_loan_automatically_guarded", guarded)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(
            service._acquire_active_loan_automatically, range(6)
        ))
    assert len(results) == 6
    assert counters["peak"] == 1
