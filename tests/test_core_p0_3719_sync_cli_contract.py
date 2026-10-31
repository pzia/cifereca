from types import SimpleNamespace

import pytest

from cifereca.core.sync_cli_contract import (
    SyncSelectionError, require_exact_sync_account, safe_sync_result,
    select_sync_accounts,
)


def _account(identity="p1", provider="future-provider", credential="c1", account="a1"):
    return SimpleNamespace(identity_id=identity, provider_id=provider,
                           credential_id=credential, unique_id=account)


def test_unknown_provider_is_not_special_cased():
    account = _account()
    assert require_exact_sync_account(
        [account], provider="future-provider", identity="p1", credential="c1"
    ) is account


def test_cross_profile_and_multi_account_are_not_implicitly_selected():
    accounts = [_account(), _account(identity="p2"), _account(account="a2")]
    with pytest.raises(SyncSelectionError, match="ambiguous_source_selection"):
        require_exact_sync_account(
            accounts, provider="future-provider", identity="p1", credential="c1"
        )
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
                                   identity="p1", credential="c1")


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
