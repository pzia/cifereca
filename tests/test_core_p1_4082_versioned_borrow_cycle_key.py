"""#4082: versioned key derivation is scoped and never grants a borrow retry."""
from dataclasses import replace

import pytest

from cifereca.core.borrow_action_scope import borrow_claim_key, borrow_cycle_claim_key
from cifereca.models import Account, SearchResult


def _scope():
    account = Account(identity_id="alice", provider_id="bnf-pret",
                      credential_id="credential-a", unique_id="borrower-a")
    item = SearchResult(
        id="ui-row-a", record_id="edition-a", docbase="BNF_PRET",
        medium="digital", title="Synthetic", author=None, type="Livre numérique",
        format=None, publisher=None, published_at=None, description=None,
        thumbnail_url=None, friendly_url=None, scenario="BNF_PRET",
        source_provider="bnf-pret", source_credential="credential-a",
    )
    return account, item


def test_first_generation_is_existing_v1_checkpoint():
    account, item = _scope()
    assert borrow_cycle_claim_key(account, item, 1) == borrow_claim_key(account, item)
    assert borrow_cycle_claim_key(account, item, 2).startswith("loan.borrow.v2:")
    assert borrow_cycle_claim_key(account, item, 2) != borrow_cycle_claim_key(account, item, 3)
    assert borrow_cycle_claim_key(account, item, 2) == borrow_cycle_claim_key(account, item, 2)


@pytest.mark.parametrize("generation", [None, False, True, 0, -1, 1.0, "2", 1_000_001])
def test_invalid_generation_cannot_mint_claim(generation):
    account, item = _scope()
    assert borrow_cycle_claim_key(account, item, generation) is None


@pytest.mark.parametrize("changed", [
    "identity", "credential", "borrower", "edition", "docbase", "collection",
])
def test_generation_is_isolated_by_exact_borrow_scope(changed):
    account, item = _scope()
    original = borrow_cycle_claim_key(account, item, 2)
    if changed == "identity":
        account = replace(account, identity_id="bob")
    elif changed == "credential":
        account = replace(account, credential_id="credential-b")
        item = replace(item, source_credential="credential-b")
    elif changed == "borrower":
        account = replace(account, unique_id="borrower-b")
    elif changed == "edition":
        item = replace(item, record_id="edition-b")
    elif changed == "docbase":
        item = replace(item, docbase="OTHER")
    else:
        item = replace(item, source_collection="other-collection")
    assert borrow_cycle_claim_key(account, item, 2) != original


def test_ui_row_change_cannot_mint_a_new_cycle():
    account, item = _scope()
    assert borrow_cycle_claim_key(account, item, 2) == borrow_cycle_claim_key(
        account, replace(item, id="other-ui-row"), 2,
    )


def test_mismatched_credential_fails_closed():
    account, item = _scope()
    assert borrow_cycle_claim_key(account, replace(item, source_credential="other"), 2) is None
