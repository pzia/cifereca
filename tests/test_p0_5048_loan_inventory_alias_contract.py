"""P0 #5048 — descriptor-scoped aliases in loan inventory, no provider switch."""
from dataclasses import replace

import pytest

from cifereca.models import Account, Loan
from cifereca.providers.base import ProviderDescriptor
from cifereca.plugin_api.loan_inventory import LoanInventoryScope, LoanInventorySnapshot


def _account(provider: str, *, identity: str = "alice", borrower: str = "") -> Account:
    return Account(
        provider_id=provider, credential_id="credential-1",
        unique_id=borrower, identity_id=identity,
    )


def _loan(account: Account, borrower: str) -> Loan:
    return Loan(
        id="loan-1", provider=account.provider_id, record_id="edition-1",
        title="Synthetic", medium="digital",
        source_provider=account.provider_id,
        source_credential=account.credential_id,
        source_account_id=borrower,
        identity_id=account.identity_id,
    )


@pytest.mark.parametrize("provider,alias", [("synthetic-a", "main"), ("synthetic-b", "primary")])
def test_descriptor_declared_alias_is_accepted_for_exact_empty_borrower(provider, alias):
    account = _account(provider)
    descriptor = ProviderDescriptor(id=provider, existing_primary_loan_account_alias=alias)
    scope = LoanInventoryScope.for_account(account, descriptor=descriptor)
    snapshot = LoanInventorySnapshot(scope=scope, loans=(_loan(account, alias),))
    assert len(snapshot.loans) == 1
    assert scope.primary_account_alias == alias
    # The same producer row, without an attested descriptor, is not admitted.
    with pytest.raises(ValueError, match="mixed_loan_inventory_scope"):
        LoanInventorySnapshot(scope=LoanInventoryScope.for_account(account),
                              loans=(_loan(account, alias),))


def test_crossed_owner_provider_credential_and_account_fail_closed():
    account = _account("synthetic-a")
    desc = ProviderDescriptor(id=account.provider_id, existing_primary_loan_account_alias="main")
    scope = LoanInventoryScope.for_account(account, descriptor=desc)
    base = _loan(account, "main")
    for foreign in (
        replace(base, identity_id="bob"),
        replace(base, source_provider="synthetic-b"),
        replace(base, source_credential="credential-2"),
        replace(base, source_account_id="linked"),
    ):
        with pytest.raises(ValueError, match="mixed_loan_inventory_scope"):
            LoanInventorySnapshot(scope=scope, loans=(foreign,))


def test_alias_not_granted_to_linked_borrower_or_other_descriptor():
    linked = _account("synthetic-a", borrower="linked")
    desc = ProviderDescriptor(id="synthetic-a", existing_primary_loan_account_alias="main")
    scope = LoanInventoryScope.for_account(linked, descriptor=desc)
    assert scope.primary_account_alias is None
    with pytest.raises(ValueError, match="mixed_loan_inventory_scope"):
        LoanInventorySnapshot(scope=scope, loans=(_loan(linked, "main"),))
    with pytest.raises(ValueError, match="invalid_loan_inventory_descriptor"):
        LoanInventoryScope.for_account(linked, descriptor=ProviderDescriptor(id="synthetic-b"))


@pytest.mark.parametrize("alias", ["", "x\x00y", "x" * 1025])
def test_malformed_descriptor_alias_refused(alias):
    account = _account("synthetic-a")
    desc = ProviderDescriptor(id="synthetic-a", existing_primary_loan_account_alias=alias)
    with pytest.raises(ValueError, match="invalid_loan_inventory_primary_alias"):
        LoanInventoryScope.for_account(account, descriptor=desc)


def test_scope_requires_exact_account_and_no_manual_alias_for_linked_account():
    with pytest.raises(ValueError, match="invalid_loan_inventory_primary_alias"):
        LoanInventoryScope("alice", "synthetic-a", "credential-1", "linked", "main")
    account = _account("synthetic-a")
    desc = ProviderDescriptor(id="synthetic-a")
    scope = LoanInventoryScope.for_account(account, descriptor=desc)
    assert scope.primary_account_alias is None
    with pytest.raises(ValueError, match="mixed_loan_inventory_scope"):
        LoanInventorySnapshot(scope=scope, loans=(_loan(account, "main"),))
