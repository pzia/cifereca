"""#5975: ADEPT borrower conflicts must not stringify plugin account fields.

Static native reads only, across providers and credentials. This is a negative
admission guard, never a source of an ADEPT entitlement or profile grant.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from cifereca.services import LibraryService


class Hostile:
    def __str__(self):
        raise AssertionError("plugin __str__ cannot establish borrower owner")

    def __bool__(self):
        raise AssertionError("plugin __bool__ cannot establish borrower owner")

    def __eq__(self, other):
        raise AssertionError("plugin __eq__ cannot establish borrower owner")


class HostileText(str):
    def __str__(self):
        raise AssertionError("untrusted str subclass cannot establish borrower")


def _row(*, provider="future-lender", credential="credential-a",
         borrower="reader-a", identity="alice", primary=False):
    return NS(provider_id=provider, credential_id=credential,
              unique_id=borrower, identity_id=identity,
              is_primary=primary)


def _conflict(accounts, *, provider="future-lender",
              borrower="reader-a", identity="alice",
              credential="credential-a"):
    return LibraryService._borrower_identity_conflict(
        accounts, provider_id=provider, account_id=borrower,
        identity_id=identity, credential_id=credential,
    )


def test_two_synthetic_suppliers_never_share_a_borrower_owner():
    alice = _row()
    unrelated = _row(provider="other-library", identity="bob")
    assert not _conflict([alice, unrelated])
    assert _conflict([alice, unrelated], provider="other-library")
    assert not _conflict([alice, unrelated], provider="future-lender")
    assert not _conflict([alice, unrelated], provider="other-library",
                         identity="bob")


def test_one_provider_two_credentials_with_conflicting_owners_refuses():
    rows = [
        _row(),
        _row(credential="credential-b", identity="bob"),
    ]
    assert _conflict(rows)
    assert _conflict(list(reversed(rows)), identity="bob",
                     credential="credential-b")
    assert not _conflict([rows[0]])
    assert _conflict([rows[0]], credential="other-credential")


@pytest.mark.parametrize(("field", "bad"), [
    ("provider_id", HostileText("future-lender")),
    ("credential_id", HostileText("credential-a")),
    ("unique_id", HostileText("reader-a")),
    ("identity_id", HostileText("alice")),
    ("is_primary", 1),
    ("credential_id", Hostile()),
    ("unique_id", Hostile()),
    ("identity_id", Hostile()),
    ("is_primary", Hostile()),
])
def test_malformed_matching_supplier_account_cannot_attest_positive(field, bad):
    row = _row()
    setattr(row, field, bad)
    assert _conflict([row])


@pytest.mark.parametrize(("field", "bad"), [
    ("provider", HostileText("future-lender")),
    ("borrower", HostileText("reader-a")),
    ("identity", HostileText("alice")),
    ("credential", HostileText("credential-a")),
    ("provider", Hostile()),
    ("borrower", Hostile()),
    ("identity", Hostile()),
    ("credential", Hostile()),
])
def test_malformed_loan_provenance_refuses_before_account_walk(field, bad):
    class PoisonInventory:
        def __iter__(self):
            raise AssertionError("malformed provenance must not read accounts")
    kwargs = dict(provider="future-lender", borrower="reader-a",
                  identity="alice", credential="credential-a")
    kwargs[field] = bad
    assert _conflict(PoisonInventory(), **kwargs)


def test_unrelated_provider_property_is_never_invoked():
    class ForeignAccount:
        provider_id = "foreign-provider"

        @property
        def credential_id(self):
            raise AssertionError("foreign provider property must stay inert")

    assert not _conflict([ForeignAccount(), _row()])


def test_same_provider_property_cannot_claim_credential_or_identity():
    class BadAccount:
        provider_id = "future-lender"
        unique_id = "reader-a"
        identity_id = "alice"
        is_primary = False

        @property
        def credential_id(self):
            raise AssertionError("property is not a native credential")

    assert _conflict([_row(), BadAccount()])


@pytest.mark.parametrize("primary", ["", "primary"])
def test_primary_alias_requires_one_native_explicit_connection(primary):
    row = _row(borrower="", primary=True)
    assert not _conflict([row], borrower=primary)
    assert _conflict([row, _row(borrower="", primary=True)],
                     borrower=primary)
    assert _conflict([row], borrower=primary, credential="")
    assert _conflict([row], borrower=primary, identity="bob")


def test_failed_or_truncated_account_inventory_is_never_positive():
    def truncated():
        yield _row()
        raise RuntimeError("untrusted list failed halfway")
    assert _conflict(truncated())
    assert _conflict(None)
