"""P0 #1120: borrower lookup failures never expose loan or account coordinates.

Read-only Core account lookup, no provider calls, no SQLite mutation,
no PNB ReturnDigitalLoan, ADEPT mutation or connected user account.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from cifereca.core.loan_accounts import account_for_loan
from cifereca.providers import ProviderConfigurationError


PRIVATE = "https://private.invalid/loan.acsm?auth=NEVER_LEAK_BORROWER"


class HostileLoanId:
    def __str__(self):
        raise AssertionError("Never stringify a loan ID in a lookup exception")

    def __repr__(self):
        raise AssertionError("Never repr a loan ID in a lookup exception")


@pytest.mark.parametrize(("borrower", "expected"), [
    (PRIVATE, "Compte emprunteur introuvable pour ce prêt"),
    (None, "Aucun compte connecté pour ce prêt"),
])
def test_exact_lookup_error_omits_all_source_and_account_coordinates(borrower, expected):
    loan = NS(
        id=HostileLoanId(), source_provider="paris",
        source_credential=PRIVATE, source_account_id=borrower,
    )
    error = ProviderConfigurationError
    with pytest.raises(ProviderConfigurationError) as caught:
        account_for_loan(
            loan, accounts=[], current_account_get=lambda: None,
            configuration_error=error,
        )
    assert str(caught.value) == expected
    assert "NEVER_LEAK_BORROWER" not in str(caught.value)
    assert "private.invalid" not in str(caught.value)


def test_explicit_missing_account_never_falls_back_to_unrelated_selected_borrower():
    private = PRIVATE
    loan = NS(
        id=HostileLoanId(), source_provider="paris",
        source_credential=private, source_account_id="linked-but-removed",
    )
    unrelated = NS(
        is_primary=True, provider_id="paris",
        credential_id=private, unique_id="other",
    )
    with pytest.raises(ProviderConfigurationError) as caught:
        account_for_loan(
            loan, accounts=[unrelated],
            current_account_get=lambda: pytest.fail(
                "Explicit borrower must not consult selected account"
            ),
            configuration_error=ProviderConfigurationError,
        )
    assert str(caught.value) == "Compte emprunteur introuvable pour ce prêt"
    assert "linked-but-removed" not in str(caught.value)


def test_exact_connected_account_success_is_unchanged():
    account = NS(
        is_primary=False, provider_id="paris",
        credential_id=PRIVATE, unique_id="linked",
    )
    loan = NS(
        id="loan-1", source_provider="paris",
        source_credential=PRIVATE, source_account_id="linked",
    )
    assert account_for_loan(
        loan, accounts=[account], current_account_get=lambda: None,
        configuration_error=ProviderConfigurationError,
    ) is account
