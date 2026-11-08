"""P0 #5055: observed-loan coordinates are a read-only display join."""
from types import SimpleNamespace as NS

import pytest

from cifereca.core import observed_loan_scope as generic
from cifereca.core import bnf_import_provenance as bnf


def _loan(**changes):
    row = dict(identity_id="reader", source_credential="card-a",
               source_account_id="borrower-a", id="loan-a",
               record_id="edition-a", source_provider="future-lender")
    row.update(changes)
    return NS(**row)


def test_provider_neutral_observation_never_deduces_provenance_or_right():
    loan = _loan()
    scope = generic.observed_loan(loan)
    assert scope == {
        "identity_id": "reader", "credential_id": "card-a",
        "account_id": "borrower-a", "loan_id": "loan-a",
        "item_id": "edition-a",
    }
    assert generic.scope_key(scope) == (
        "reader", "card-a", "borrower-a", "loan-a", "edition-a"
    )
    assert not any(key in scope for key in (
        "source_provider", "entitlement_id", "acquisition_id",
        "authorized", "owned", "loan_valid",
    ))


def test_bnf_existing_public_functions_remain_identical():
    assert bnf.FIELDS is generic.FIELDS
    assert bnf.observed_loan is generic.observed_loan
    assert bnf.scope_key is generic.scope_key
    assert bnf.scope_key(bnf.observed_loan(_loan())) == generic.scope_key(
        generic.observed_loan(_loan())
    )


@pytest.mark.parametrize(("field", "bad"), [
    ("identity_id", ""), ("credential_id", None), ("account_id", 0),
    ("loan_id", []), ("item_id", {"source": "hostile"}),
])
def test_missing_or_malformed_exact_borrower_scope_is_not_a_link(field, bad):
    coordinates = generic.observed_loan(_loan())
    coordinates[field] = bad
    assert generic.scope_key(coordinates) is None


def test_foreign_field_or_missing_field_and_wrong_container_are_refused():
    coordinates = generic.observed_loan(_loan())
    assert generic.scope_key(dict(coordinates, purchase=True)) is None
    coordinates.pop("credential_id")
    assert generic.scope_key(coordinates) is None
    assert generic.scope_key(list(coordinates.items())) is None
    assert generic.scope_key(None) is None


def test_distinct_accounts_and_loans_never_share_a_scope_key():
    original = generic.scope_key(generic.observed_loan(_loan()))
    for change in (
        dict(identity_id="other"), dict(source_credential="card-b"),
        dict(source_account_id="borrower-b"), dict(id="loan-b"),
        dict(record_id="edition-b"),
    ):
        assert generic.scope_key(
            generic.observed_loan(_loan(**change))
        ) != original
