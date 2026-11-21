"""P0 #4496/#7244: ambiguous historical ADEPT attention never licenses a replay.

Exercise the actual SQLite legacy read path with no provider, network, or
ADEPT process. Corrupted metadata cannot be interpreted as a different loan.
"""
from __future__ import annotations

import sqlite3

import pytest

from cifereca.core.canonical_loan_retry_guard import historical_adept_ledger_hold
from cifereca.core.source_convergence import SourceKey
from cifereca.plugin_api.loan_inventory import LoanInventoryScope
from cifereca.plugin_api.provider_rights_observation import ProviderRightsScope


def _historical_hold(metadata_json: str, *, right_id="loan-a", account_id="main") -> bool:
    db = sqlite3.connect(":memory:")
    try:
        db.execute(
            "CREATE TABLE acquisition_ledger ("
            "identity_id TEXT, provider_id TEXT, credential_id TEXT, "
            "account_id TEXT, item_id TEXT, status TEXT, strategy TEXT, "
            "executor TEXT, entitlement_id TEXT, metadata_json TEXT)"
        )
        db.execute(
            "INSERT INTO acquisition_ledger VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("reader", "paris", "card", account_id, "edition-a",
             "attention_required", "loan_auto", "kobo-adept",
             "entitlement-a", metadata_json),
        )
        scope = LoanInventoryScope("reader", "paris", "card", account_id)
        source = SourceKey(
            ProviderRightsScope("reader", "paris", "card", account_id),
            "edition-a", right_id, "loan", "branch",
        )
        return historical_adept_ledger_hold(db, scope, source)
    finally:
        db.close()


@pytest.mark.parametrize("metadata", [
    '{"provider_loan_id":"loan-a","provider_loan_id":"loan-b"}',
    '{"provider_loan_id":"loan-b","provider_loan_id":"loan-a"}',
    '{"provider_loan_id":"loan-b","nested":{"flag":0,"flag":1}}',
    '{"provider_loan_id":"loan-b","nested":[{"flag":0,"flag":1}]}',
])
def test_duplicate_field_any_depth_keeps_historical_adept_fence(metadata):
    assert _historical_hold(metadata)


@pytest.mark.parametrize("metadata", [
    '{"provider_loan_id":',
    '["unexpected"]',
    '{"provider_loan_id":17}',
    '{"provider_loan_id":""}',
])
def test_malformed_legacy_attention_remains_explicit_only(metadata):
    assert _historical_hold(metadata)


def test_unambiguous_other_loan_does_not_block_exact_source():
    assert not _historical_hold('{"provider_loan_id":"loan-b"}')


@pytest.mark.parametrize("account_id", ["main", ""])
def test_unambiguous_same_loan_remains_blocked_in_exact_account(account_id):
    assert _historical_hold('{"provider_loan_id":"loan-a"}', account_id=account_id)


def test_missing_legacy_loan_id_remains_conservative():
    assert _historical_hold('{"legacy_receipt":"unknown"}')
