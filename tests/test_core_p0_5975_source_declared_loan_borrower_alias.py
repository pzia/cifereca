"""P0 #5975/#5056: exact borrower provenance, not the first matching row.

An installed provider's immutable descriptor owns the primary-account
observation convention. A duplicate upstream account or an ambiguous literal
alias never becomes a connected borrower for a protected loan action.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from cifereca.core.loan_accounts import account_for_loan
from cifereca.models import Account, Loan
from cifereca.providers import (
    ProviderCapabilities, ProviderConfigurationError, ProviderDescriptor,
)
from cifereca.services import LibraryService


def _account(uid="", *, provider="synthetic", credential="cred", primary=True):
    return Account(
        unique_id=uid, provider_id=provider,
        credential_id=credential, identity_id="alice",
        is_primary=primary,
    )


def _loan(borrower, *, provider="synthetic", credential="cred"):
    return Loan(
        id="loan", record_id="edition", provider="TEST", medium="digital",
        title="Synthetic loan", source_provider=provider,
        source_credential=credential, source_account_id=borrower,
        identity_id="alice",
    )


def _service(accounts, *, alias="reader-main", registered=True, valid=True):
    service = LibraryService.__new__(LibraryService)
    service.accounts = accounts
    descriptor = ProviderDescriptor(
        id="synthetic" if valid else "other",
        display_name="Synthetic", description="Test",
        state="ready", capabilities=ProviderCapabilities(loans=True),
        existing_primary_loan_account_alias=alias,
    )
    plugin = NS(descriptor=descriptor) if registered else None
    service.registry = NS(maybe_get=lambda _provider: plugin)
    service.current_account = _account("foreign", credential="other")
    return service


def test_installed_source_primary_alias_proves_only_one_empty_primary():
    primary = _account()
    service = _service([primary])
    assert service._account_for_loan(_loan("reader-main")) is primary
    assert service._account_for_loan(_loan("")) is primary
    # The old Core-literal alias must not substitute for the installed plugin.
    with pytest.raises(ProviderConfigurationError):
        service._account_for_loan(_loan("primary"))


@pytest.mark.parametrize("alias", [
    None, "", object(), "x" * 129, "UPPERCASE", "reader/main", "has space",
])
def test_invalid_or_absent_alias_does_not_admit_primary_by_name(alias):
    service = _service([_account()], alias=alias)
    with pytest.raises(ProviderConfigurationError):
        service._account_for_loan(_loan("reader-main"))


def test_invalid_descriptor_does_not_allow_any_provider_alias():
    service = _service([_account()], valid=False)
    with pytest.raises(ProviderConfigurationError):
        service._account_for_loan(_loan("reader-main"))


def test_empty_borrower_never_selects_linked_account_without_primary_proof():
    linked_empty = _account("", primary=False)
    service = _service([linked_empty])
    with pytest.raises(ProviderConfigurationError, match="introuvable"):
        service._account_for_loan(_loan(""))
    with pytest.raises(ProviderConfigurationError, match="introuvable"):
        service._account_for_loan(_loan("reader-main"))


def test_same_literal_upstream_account_and_primary_alias_is_ambiguous():
    primary = _account()
    literal = _account("reader-main", primary=False)
    service = _service([primary, literal])
    with pytest.raises(ProviderConfigurationError, match="introuvable"):
        service._account_for_loan(_loan("reader-main"))
    # The real reader ID still resolves uniquely when the alias differs.
    service = _service([primary, literal], alias="different")
    assert service._account_for_loan(_loan("reader-main")) is literal


@pytest.mark.parametrize("borrower", ["", "linked", "reader-main"])
def test_duplicate_borrower_accounts_are_never_first_row_wins(borrower):
    accounts = [
        _account(borrower if borrower != "reader-main" else "", primary=True),
        _account(borrower if borrower != "reader-main" else "", primary=True),
    ]
    service = _service(accounts)
    with pytest.raises(ProviderConfigurationError, match="introuvable"):
        service._account_for_loan(_loan(borrower))


def test_same_reader_at_another_credential_does_not_create_a_collision():
    expected = _account("linked", primary=False)
    service = _service([
        _account("linked", credential="other", primary=False), expected,
    ])
    assert service._account_for_loan(_loan("linked")) is expected


def test_uninstalled_legacy_alias_remains_only_as_compatibility():
    primary = _account(provider="paris")
    service = LibraryService.__new__(LibraryService)
    service.accounts = [primary]
    # Compatibility only: the installed registry contracts and later mutable
    # loan-action scope still independently require a connected provider.
    assert service._account_for_loan(
        _loan("primary", provider="paris"),
    ) is primary


def test_malformed_registry_cannot_reactivate_historical_primary_alias():
    service = _service([_account()])
    def broken_registry(_provider):
        raise RuntimeError("untrusted registry failure")
    service.registry = NS(maybe_get=broken_registry)
    with pytest.raises(ProviderConfigurationError, match="introuvable"):
        service._account_for_loan(_loan("primary"))


def test_hostile_provider_key_is_rejected_before_registry_lookup():
    service = _service([_account()])
    class HostileProvider:
        def __hash__(self):
            raise AssertionError("untrusted provider hash must not run")

        def __eq__(self, _other):
            raise AssertionError("untrusted provider comparison must not run")

    service.registry = NS(maybe_get=lambda _key: pytest.fail(
        "untrusted provider reached the installed registry"
    ))
    loan = _loan("")
    loan.source_provider = HostileProvider()
    with pytest.raises(ProviderConfigurationError, match="non attesté"):
        service._account_for_loan(loan)


@pytest.mark.parametrize("field,value", [
    ("source_provider", "foreign"),
    ("source_credential", "other"),
    ("source_account_id", "other-reader"),
])
def test_registry_cannot_retarget_loan_scope_before_account_selection(
    field, value,
):
    loan = _loan("reader-main")
    service = _service([
        _account(),
        _account("", provider="foreign"),
        _account("", credential="other"),
        _account("other-reader", primary=False),
    ])
    def mutate_during_lookup(_key):
        setattr(loan, field, value)
        return None
    service.registry = NS(maybe_get=mutate_during_lookup)
    with pytest.raises(ProviderConfigurationError, match="non attesté"):
        service._account_for_loan(loan)


def test_malformed_borrower_is_rejected_before_registry_lookup():
    service = _service([_account()])
    service.registry = NS(maybe_get=lambda _key: pytest.fail(
        "invalid borrower reached provider registry"
    ))
    loan = _loan("reader-main")
    loan.source_account_id = False
    with pytest.raises(ProviderConfigurationError, match="non attesté"):
        service._account_for_loan(loan)


def test_loan_source_fields_must_be_native_and_no_private_values_leak():
    primary = _account()
    class SourceStr(str):
        def __eq__(self, _other):
            raise AssertionError("untrusted equality must not be invoked")
    row = _loan("reader-main")
    row.source_provider = SourceStr("synthetic")
    with pytest.raises(ProviderConfigurationError, match="non attesté") as caught:
        account_for_loan(
            row, accounts=[primary],
            configuration_error=ProviderConfigurationError,
            primary_alias="reader-main",
        )
    assert "synthetic" not in str(caught.value)


@pytest.mark.parametrize("alias", ["reader-main", "principal-v2", "primary"])
def test_return_preflight_accepts_installed_alias_not_core_literal(alias):
    from cifereca.core.loan_return_guard import return_key

    primary = _account()
    service = _service([primary], alias=alias)
    session = NS(connected=True)
    service._sessions = {service._session_key("synthetic", "cred"): session}
    account, resolved, key = service._return_loan_identity(_loan(alias))
    assert account is primary and resolved is session
    assert key == return_key("synthetic", "cred", alias, "loan")


@pytest.mark.parametrize("borrower,alias,uid,primary", [
    ("primary", "reader-main", "", True),
    ("reader-main", None, "", True),
    ("reader-main", "reader-main", "", False),
    ("", "reader-main", "primary", True),
    ("", "reader-main", "", False),
    ("reader-main", "reader-main", "other", True),
])
def test_return_preflight_independently_refuses_unproven_alias(
    borrower, alias, uid, primary,
):
    from cifereca.plugin_api import AttentionRequired

    account = _account(uid, primary=primary)
    service = _service([account], alias=alias)
    # A substituted resolver cannot revive the old alias shortcut.
    service._account_for_loan = lambda _loan: account
    service._session_key = lambda *_: pytest.fail("unproven alias reached session")
    with pytest.raises(AttentionRequired) as denied:
        service._return_loan_identity(_loan(borrower))
    assert denied.value.code == "loan_return_borrower_mismatch"


def test_return_preflight_literal_borrower_remains_independent_of_alias():
    linked = _account("primary", primary=False)
    service = _service([linked], alias="reader-main")
    session = NS(connected=True)
    service._sessions = {service._session_key("synthetic", "cred"): session}
    account, resolved, _key = service._return_loan_identity(_loan("primary"))
    assert account is linked and resolved is session
