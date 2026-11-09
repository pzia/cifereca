"""P0 #5048: same negative legacy source-ID alias rule for all providers."""
import pytest

from cifereca.core.legacy_source_account_id import (
    ambiguous_legacy_source_account_id as blocked,
)


@pytest.mark.parametrize(("provider", "credential", "account", "refuse"), [
    ("kobo", "card-a", "", False),
    ("bnf-pret", "card-a", "", False),
    ("numilog", "card-a", "real-borrower", False),
    ("future-store", "card-a", "real-borrower", False),
    ("kobo", "card-a", "kobo:card-a:primary", True),
    ("bnf-pret", "card-a", "bnf-pret:card-a:primary", True),
    ("numilog", "card-a", "numilog:card-a:primary", True),
    ("future-store", "card-a", "future-store:card-a:primary", True),
    ("kobo", "card-a", "kobo:card-b:primary", False),
    ("kobo", "card-a", "bnf-pret:card-a:primary", False),
    ("kobo", "card-a", " leading", True),
    ("kobo", "card-a", "trailing ", True),
    ("kobo", "card-a", None, True),
    ("kobo", "card-a", 7, True),
    ("kobo", "", "account", True),
    ("", "card-a", "account", True),
])
def test_exact_legacy_alias_guard(provider, credential, account, refuse):
    assert blocked(provider, credential, account) is refuse


def test_rule_stays_independent_of_provider_brand_or_entitlement():
    for provider in ("kobo", "numilog", "bnf-pret", "future-store"):
        credential = "credential-a"
        assert blocked(provider, credential, provider + ":credential-a:primary")
        assert not blocked(provider, credential, "unique-borrower")
        assert not blocked(provider, credential, "")
