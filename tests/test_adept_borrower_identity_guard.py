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
