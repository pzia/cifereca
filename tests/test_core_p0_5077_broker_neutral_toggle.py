"""P0 #5077: neutral source toggle facade and fail-closed CAS admission.

Synthetic offline storage, no provider login/HTTP/ADEPT.
"""
from pathlib import Path
import ast

import pytest

from cifereca.core.source_sync_consent import (
    policy_key, scope_for_account, set_inventory_source_enabled,
)
from cifereca.storage import Storage
from tests.test_core_0978_source_state import fake_broker


def test_broker_has_no_legacy_policy_or_generation_dispatch():
    source = (Path(__file__).resolve().parents[1]
              / "src/cifereca/brokers.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    broker = next(x for x in tree.body
                  if isinstance(x, ast.ClassDef)
                  and x.name == "SynchronizationBrokerImpl")
    toggle = next(x for x in broker.body
                  if isinstance(x, ast.FunctionDef) and x.name == "set_enabled")
    body = ast.unparse(toggle)
    assert "set_inventory_source_enabled" in body
    assert "_consent_generation_key" not in body
    assert "legacy_source_consent_switch_present" not in body


def test_internal_toggle_rejects_wrong_scope_without_writing_anything(tmp_path):
    storage = Storage(tmp_path / "library.sqlite3")
    try:
        broker = fake_broker(storage)
        account = broker.service.accounts[0]
        scope = scope_for_account(account)
        source_id = broker._source_id(account)
        actual = broker._preference_key(source_id)
        foreign = actual + ".other-account"
        with pytest.raises(ValueError, match="source_sync_toggle_scope_mismatch"):
            set_inventory_source_enabled(broker.service, account, False, foreign)
        assert storage.metadata_get(foreign) is None
        assert storage.metadata_get(actual) is None
        assert storage.metadata_get(policy_key(scope)) is None
    finally:
        storage.close()


def test_detached_account_cannot_write_legacy_inventory_preference(tmp_path):
    from dataclasses import replace
    storage = Storage(tmp_path / "library.sqlite3")
    try:
        broker = fake_broker(storage)
        original = broker.service.accounts[0]
        detached = replace(original)
        assert detached == original and detached is not original
        exact_key = broker._preference_key(broker._source_id(original))
        with pytest.raises(ValueError, match="source_sync_consent_scope_changed"):
            set_inventory_source_enabled(
                broker.service, detached, True, exact_key,
            )
        assert storage.metadata_get(exact_key) is None
        assert storage.metadata_get(policy_key(scope_for_account(original))) is None
    finally:
        storage.close()


@pytest.mark.parametrize("provider", ["paris", "kobo", "bnf-pret", "numilog"])
def test_literal_primary_alias_cannot_write_other_accounts_legacy_inventory(
    tmp_path, provider,
):
    storage = Storage(tmp_path / "library.sqlite3")
    try:
        broker = fake_broker(storage)
        account = broker.service.accounts[0]
        account.provider_id = provider
        account.unique_id = provider + ":" + account.credential_id + ":primary"
        alias_key = broker._preference_key(broker._source_id(account))
        from cifereca.core.legacy_source_identifier import legacy_source_id
        assert broker._source_id(account) == legacy_source_id(
            account.identity_id, provider, account.credential_id, "",
        )
        with pytest.raises(ValueError, match="source_sync_toggle_scope_mismatch"):
            set_inventory_source_enabled(broker.service, account, False, alias_key)
        assert storage.metadata_get(alias_key) is None
        assert storage.metadata_get(policy_key(scope_for_account(account))) is None
    finally:
        storage.close()


def test_legacy_inventory_toggle_does_not_create_cas_opt_in(tmp_path):
    storage = Storage(tmp_path / "library.sqlite3")
    try:
        broker = fake_broker(storage)
        account = broker.service.accounts[0]
        account.provider_id = "kobo"
        scope = scope_for_account(account)
        source_id = broker._source_id(account)
        broker.set_enabled(source_id, True)
        assert storage.metadata_get(policy_key(scope)) is None
        broker.set_enabled(source_id, False)
        assert storage.metadata_get(policy_key(scope)) is None
    finally:
        storage.close()
