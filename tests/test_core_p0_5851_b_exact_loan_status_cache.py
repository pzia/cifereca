"""#5851 / Agent B: ADEPT loan display is scoped to the source, never GTK state.

Synthetic account cache only: no provider calls, ACSM, mutations or remote I/O.
"""
from dataclasses import asdict
from types import SimpleNamespace as NS

import pytest

from cifereca.models import Account, Loan
from cifereca.services import LibraryService, _loan_cache_key


PROVIDER = "synthetic-lender"


def _account(identity, credential, borrower):
    return Account(
        provider_id=PROVIDER, credential_id=credential,
        unique_id=borrower, identity_id=identity,
        is_primary=False,
    )


def _loan(account, loan_id="same-upstream-loan"):
    return Loan(
        id=loan_id, provider="synthetic", medium="digital", state="active",
        title="Fictional edition", record_id="same-edition",
        source_provider=PROVIDER, source_credential=account.credential_id,
        source_account_id=account.unique_id, identity_id=account.identity_id,
    )


def _row(account, *, loan_id="same-upstream-loan"):
    return {
        "identity_id": account.identity_id,
        "provider_id": account.provider_id,
        "credential_id": account.credential_id,
        "account_id": account.unique_id,
        "item_id": "same-edition",
        "metadata": {"provider_loan_id": loan_id},
        "entitlement_id": None,
    }


def _host(accounts, cache):
    reads = []
    def cached(namespace, key):
        reads.append((namespace, key))
        return cache.get((namespace, key))
    storage = NS(
        cache_get=cached,
        entitlements=lambda **kwargs: [],
    )
    return NS(
        accounts=accounts, storage=storage,
        _sessions={}, _session_key=lambda provider, credential: (provider, credential),
        # Deliberately poisoned GTK snapshot; must never be inspected.
        last_loans=[_loan(_account("foreign", "foreign-card", "foreign-reader"))],
        current_identity=NS(identity_id="foreign"),
    ), reads


def _check(host, row):
    return LibraryService._provider_loan_active_for_acquisition(host, row)


def test_exact_cached_source_is_not_replaced_by_current_gtk_profile():
    alice = _account("alice", "card-A", "reader-A")
    bob = _account("bob", "card-B", "reader-B")
    cache = {
        ("account", _loan_cache_key(alice)): [asdict(_loan(alice))],
        ("account", _loan_cache_key(bob)): [asdict(_loan(bob, "another-loan"))],
    }
    host, reads = _host([alice, bob], cache)
    assert _check(host, _row(alice)) is True
    assert _check(host, _row(bob)) is False
    host.current_identity = NS(identity_id="alice")
    host.last_loans = [_loan(bob, "same-upstream-loan")]
    assert _check(host, _row(alice)) is True
    assert _check(host, _row(bob)) is False
    assert reads == [
        ("account", _loan_cache_key(alice)),
        ("account", _loan_cache_key(bob)),
        ("account", _loan_cache_key(alice)),
        ("account", _loan_cache_key(bob)),
    ]


def test_no_cache_never_falls_back_to_foreign_gtk_loan_or_provider_get():
    alice = _account("alice", "card-A", "reader-A")
    host, reads = _host([alice], {})
    host.last_loans = [_loan(alice)]
    assert _check(host, _row(alice)) is False
    assert reads == [("account", _loan_cache_key(alice))]


@pytest.mark.parametrize("corruption", ["foreign_identity", "foreign_credential", "foreign_borrower", "wrong_type"])
def test_untrusted_cross_profile_cache_cannot_report_active_loan(corruption):
    alice = _account("alice", "card-A", "reader-A")
    cached = asdict(_loan(alice))
    if corruption == "foreign_identity":
        cached["identity_id"] = "bob"
    elif corruption == "foreign_credential":
        cached["source_credential"] = "card-B"
    elif corruption == "foreign_borrower":
        cached["source_account_id"] = "reader-B"
    else:
        cached = {"malformed": "not a loan"}
    host, _ = _host([alice], {("account", _loan_cache_key(alice)): [cached]})
    assert _check(host, _row(alice)) is False


def test_ambiguous_same_account_coordinates_refuse_cached_positive():
    alice = _account("alice", "card-A", "reader-A")
    cache = {("account", _loan_cache_key(alice)): [asdict(_loan(alice))]}
    host, reads = _host([alice, alice], cache)
    assert _check(host, _row(alice)) is False
    assert reads == []


@pytest.mark.parametrize("missing", ["identity_id", "provider_id", "credential_id", "account_id"])
def test_missing_source_coordinates_refuse_before_storage_or_plugin(missing):
    alice = _account("alice", "card-A", "reader-A")
    host, reads = _host([alice], {})
    row = _row(alice)
    row[missing] = None
    assert _check(host, row) is False
    assert reads == []


def test_provenance_does_not_infer_activity_from_a_legacy_entitlement():
    alice = _account("alice", "card-A", "reader-A")
    host, _ = _host([alice], {})
    host.storage.entitlements = lambda **kwargs: [
        {"entitlement_id": "legacy", "loan_id": "same-upstream-loan"},
    ]
    row = _row(alice)
    row["entitlement_id"] = "legacy"
    assert _check(host, row) is False
