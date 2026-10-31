from types import SimpleNamespace

import pytest

from cifereca.core.sync_cli_contract import (
    SyncSelectionError, require_exact_sync_account, safe_sync_result,
    select_sync_accounts, require_unique_sync_account,
)


def _account(identity="p1", provider="future-provider", credential="c1", account="a1"):
    return SimpleNamespace(identity_id=identity, provider_id=provider,
                           credential_id=credential, unique_id=account)


def test_unknown_provider_is_not_special_cased():
    account = _account()
    assert require_exact_sync_account(
        [account], provider="future-provider", identity="p1", credential="c1", account_id="a1"
    ) is account


def test_cross_profile_and_multi_account_are_not_implicitly_selected():
    accounts = [_account(), _account(identity="p2"), _account(account="a2")]
    with pytest.raises(SyncSelectionError, match="source_scope_required"):
        require_exact_sync_account(
            accounts, provider="future-provider", identity="p1", credential="c1"
        )
    assert require_exact_sync_account(
        accounts, provider="future-provider", identity="p1",
        credential="c1", account_id="a1",
    ) is accounts[0]
    assert require_exact_sync_account(
        accounts, provider="future-provider", identity="p1",
        credential="c1", account_id="a2",
    ) is accounts[2]
    assert len(select_sync_accounts(accounts, identity="p2")) == 1


def test_duplicate_binding_and_missing_scope_fail_closed():
    duplicate = [_account(), _account()]
    with pytest.raises(SyncSelectionError, match="ambiguous_source_binding"):
        select_sync_accounts(duplicate)
    with pytest.raises(SyncSelectionError, match="source_scope_required"):
        require_exact_sync_account([_account()], provider="future-provider")
    with pytest.raises(SyncSelectionError, match="source_not_found"):
        require_exact_sync_account([_account()], provider="other",
                                   identity="p1", credential="c1", account_id="a1")


def test_invalid_selectors_refused():
    with pytest.raises(SyncSelectionError, match="invalid_source_selector"):
        select_sync_accounts([_account()], provider="future-provider\nsecret")


def test_result_does_not_leak_provider_payload_or_fake_busy_success():
    raw = {"status": "busy", "reason": "operation_busy", "errors": 1,
           "secret": "bearer token", "path": "/private/item.epub"}
    assert safe_sync_result(raw) == {
        "status": "busy", "errors": 1, "code": "operation_busy"
    }
    assert safe_sync_result({"status": "impossible", "errors": -4}) == {
        "status": "failed"
    }


def test_empty_primary_account_id_requires_explicit_selection():
    accounts = [_account(account=""), _account(account="secondary")]
    with pytest.raises(SyncSelectionError, match="source_scope_required"):
        require_exact_sync_account(accounts, provider="future-provider",
                                   identity="p1", credential="c1")
    assert require_exact_sync_account(accounts, provider="future-provider",
                                      identity="p1", credential="c1", account_id="") is accounts[0]


def test_unique_source_selection_needs_only_identity_when_unambiguous():
    accounts = [_account(identity="p1"), _account(identity="p2")]
    assert require_unique_sync_account(accounts, identity="p1") is accounts[0]
    assert require_unique_sync_account(
        accounts, identity="p2", provider="future-provider",
    ) is accounts[1]


def test_identity_only_refuses_ambiguous_sources_without_guessing_ids():
    accounts = [_account(), _account(credential="c2"), _account(identity="p2")]
    with pytest.raises(SyncSelectionError, match="account_ambiguous"):
        require_unique_sync_account(accounts, identity="p1")
    assert require_unique_sync_account(
        accounts, identity="p1", credential="c2",
    ) is accounts[1]


def test_no_identity_and_missing_source_fail_closed():
    with pytest.raises(SyncSelectionError, match="source_scope_required"):
        require_unique_sync_account([_account()], provider="future-provider")
    with pytest.raises(SyncSelectionError, match="source_not_found"):
        require_unique_sync_account([_account()], identity="missing")


def test_duplicate_identity_account_binding_is_not_auto_selected():
    with pytest.raises(SyncSelectionError, match="ambiguous_source_binding"):
        require_unique_sync_account([_account(), _account()], identity="p1")


def test_empty_upstream_account_id_can_be_auto_selected_when_unique():
    account = _account(account="")
    assert require_unique_sync_account([account], identity="p1") is account
