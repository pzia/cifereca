"""P0 #5048 — descriptor-scoped aliases in loan inventory, no provider switch."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from cifereca.models import Account, Loan
from cifereca.providers.base import ProviderCapabilities, ProviderDescriptor
from cifereca.plugin_api.loan_inventory import (LoanInventoryScope, LoanInventorySnapshot,
    legacy_loan_inventory, read_loan_inventory)


def _descriptor(provider: str, alias: str | None = None) -> ProviderDescriptor:
    return ProviderDescriptor(
        id=provider, display_name=provider, description="Synthetic provider",
        state="ready", capabilities=ProviderCapabilities(),
        existing_primary_loan_account_alias=alias,
    )


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
    descriptor = _descriptor(provider, alias)
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
    desc = _descriptor(account.provider_id, "main")
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
    desc = _descriptor("synthetic-a", "main")
    scope = LoanInventoryScope.for_account(linked, descriptor=desc)
    assert scope.primary_account_alias is None
    with pytest.raises(ValueError, match="mixed_loan_inventory_scope"):
        LoanInventorySnapshot(scope=scope, loans=(_loan(linked, "main"),))
    with pytest.raises(ValueError, match="invalid_loan_inventory_descriptor"):
        LoanInventoryScope.for_account(linked, descriptor=_descriptor("synthetic-b"))


@pytest.mark.parametrize("alias", ["", "x\x00y", "x" * 1025, "Primary", "a.b", "-bad", "a" * 65])
def test_malformed_descriptor_alias_refused(alias):
    account = _account("synthetic-a")
    desc = _descriptor("synthetic-a", alias)
    with pytest.raises(ValueError, match="invalid_loan_inventory_primary_alias"):
        LoanInventoryScope.for_account(account, descriptor=desc)


def test_scope_requires_exact_account_and_no_manual_alias_for_linked_account():
    with pytest.raises(ValueError, match="invalid_loan_inventory_primary_alias"):
        LoanInventoryScope("alice", "synthetic-a", "credential-1", "linked", "main")
    account = _account("synthetic-a")
    desc = _descriptor("synthetic-a")
    scope = LoanInventoryScope.for_account(account, descriptor=desc)
    assert scope.primary_account_alias is None
    with pytest.raises(ValueError, match="mixed_loan_inventory_scope"):
        LoanInventorySnapshot(scope=scope, loans=(_loan(account, "main"),))


def test_explicit_legacy_adapter_descriptor_accepts_alias_without_network():
    account = _account("synthetic-a")
    desc = _descriptor("synthetic-a", "main")
    row = _loan(account, "main")
    assert len(legacy_loan_inventory(account, [row], descriptor=desc).loans) == 1
    with pytest.raises(ValueError, match="mixed_loan_inventory_scope"):
        legacy_loan_inventory(account, [row])


def test_typed_reader_uses_same_descriptor_scope_as_producer():
    account = _account("synthetic-b")
    desc = _descriptor("synthetic-b", "other-primary")
    calls = []
    def observed(arg):
        calls.append("typed")
        return LoanInventorySnapshot(
            scope=LoanInventoryScope.for_account(arg, descriptor=desc),
            loans=(_loan(arg, "other-primary"),),
        )
    session = SimpleNamespace(
        connected=True, provider_id=account.provider_id,
        credential_id=account.credential_id,
        capabilities=SimpleNamespace(loans=True),
        loan_inventory_snapshot=observed,
    )
    result = read_loan_inventory(session, account, descriptor=desc)
    assert len(result.loans) == 1 and calls == ["typed"]
    rejected = read_loan_inventory(session, account)
    assert rejected.status == "failed" and rejected.loans == ()
    assert calls == ["typed", "typed"]


def test_legacy_reader_uses_descriptor_without_second_provider_operation():
    account = _account("synthetic-a")
    desc = _descriptor("synthetic-a", "main")
    calls = []
    session = SimpleNamespace(
        connected=True, provider_id=account.provider_id,
        credential_id=account.credential_id,
        capabilities=SimpleNamespace(loans=True),
        loans=lambda arg: calls.append("legacy") or [_loan(arg, "main")],
    )
    result = read_loan_inventory(session, account, descriptor=desc)
    assert result.status == "partial" and len(result.loans) == 1
    assert calls == ["legacy"]


def test_explicit_descriptor_without_alias_vetoes_legacy_paris_fallback():
    account = _account("paris")
    row = _loan(account, "primary")
    # No provider-name fallback is permitted, even without a descriptor.
    with pytest.raises(ValueError, match="mixed_loan_inventory_scope"):
        LoanInventorySnapshot(
            scope=LoanInventoryScope.for_account(account), loans=(row,),
        )
    checked = LoanInventoryScope.for_account(account, descriptor=_descriptor("paris"))
    assert checked.primary_alias_policy_checked is False
    with pytest.raises(ValueError, match="mixed_loan_inventory_scope"):
        LoanInventorySnapshot(scope=checked, loans=(row,))


def test_explicit_descriptor_alias_takes_priority_over_paris_legacy():
    account = _account("paris")
    scope = LoanInventoryScope.for_account(account, descriptor=_descriptor("paris", "main"))
    assert len(LoanInventorySnapshot(scope=scope, loans=(_loan(account, "main"),)).loans) == 1
    with pytest.raises(ValueError, match="mixed_loan_inventory_scope"):
        LoanInventorySnapshot(scope=scope, loans=(_loan(account, "primary"),))


def test_scope_alias_cannot_be_injected_without_explicit_policy_marker():
    with pytest.raises(ValueError, match="invalid_loan_inventory_primary_alias"):
        LoanInventoryScope("alice", "synthetic-a", "credential-1", "", "main")
    with pytest.raises(ValueError, match="invalid_loan_inventory_alias_policy"):
        LoanInventoryScope("alice", "synthetic-a", "credential-1", "", None, 1)


def test_empty_id_nonprimary_account_cannot_use_declared_alias():
    account = _account("synthetic-a")
    account.is_primary = False
    desc = _descriptor("synthetic-a", "main")
    scope = LoanInventoryScope.for_account(account, descriptor=desc)
    assert scope.primary_alias_policy_checked is False
    assert scope.primary_account_alias is None
    with pytest.raises(ValueError, match="mixed_loan_inventory_scope"):
        LoanInventorySnapshot(scope=scope, loans=(_loan(account, "main"),))
