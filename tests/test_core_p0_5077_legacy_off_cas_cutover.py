"""P0 #5077: a user disabling legacy inventory durably revokes legacy ON.

Real SQLite and synthetic BnF service; no provider HTTP, ADEPT or GET.
"""
from __future__ import annotations

import sqlite3

import pytest

from cifereca.brokers import SynchronizationBrokerImpl
from cifereca.core.source_auto_consent_bridge import _source_id, legacy_source_auto_policy
from cifereca.core.source_sync_consent import (
    read_policy, scope_for_account, policy_key, source_enablement_key,
)
from tests.test_core_p0_2055_bnf_explicit_stage import connected_core


def _seed_legacy_on(service, account):
    scope = scope_for_account(account)
    key = "sync.source.bnf_auto." + _source_id(scope)
    service.storage.metadata_set_with_revision(key, "1", key + ".revision")
    service.storage.metadata_set(source_enablement_key(scope), "1")
    return key, scope


def test_explicit_legacy_off_becomes_cas_off_and_inventory_on_cannot_restore_it(
    connected_core,
):
    service, session, account, _loan, _state, downloaded = connected_core
    key, scope = _seed_legacy_on(service, account)
    assert legacy_source_auto_policy(service.storage, scope, "loan")[0] == "on"
    broker = SynchronizationBrokerImpl(service)
    source_id = broker._source_id(account)
    broker.set_enabled(source_id, False)
    disabled = read_policy(service.storage, scope)
    assert disabled.mode == "off" and disabled.configured
    assert service.storage.metadata_get(source_enablement_key(scope)) == "0"
    assert service.storage.metadata_get(key) == "1"  # no data loss
    assert legacy_source_auto_policy(service.storage, scope, "loan")[0] == "off"

    broker.set_enabled(source_id, True)
    reenabled = read_policy(service.storage, scope)
    assert reenabled.mode == "off" and reenabled.revision != disabled.revision
    assert service.storage.metadata_get(source_enablement_key(scope)) == "1"
    assert service.storage.metadata_get(key) == "1"  # inert under generic OFF
    assert not downloaded


def test_cas_off_and_inventory_disable_commit_atomically_or_both_rollback(
    connected_core,
):
    service, session, account, _loan, _state, downloaded = connected_core
    key, scope = _seed_legacy_on(service, account)
    source_id = SynchronizationBrokerImpl(service)._source_id(account)
    cas_key = policy_key(scope)
    with service.storage._lock, service.storage._connect() as db:
        db.execute(
            "CREATE TRIGGER stop_cas BEFORE INSERT ON metadata "
            "WHEN NEW.key=? BEGIN SELECT RAISE(ABORT,'blocked_cas'); END"
            .replace("NEW.key=?", "NEW.key='" + cas_key + "'")
        )
    try:
        with pytest.raises(sqlite3.IntegrityError, match="blocked_cas"):
            SynchronizationBrokerImpl(service).set_enabled(source_id, False)
        assert read_policy(service.storage, scope).mode == "unset"
        assert service.storage.metadata_get(source_enablement_key(scope)) == "1"
        assert legacy_source_auto_policy(service.storage, scope, "loan")[0] == "on"
        assert service.storage.metadata_get(key) == "1"
        assert not downloaded
    finally:
        with service.storage._lock, service.storage._connect() as db:
            db.execute("DROP TRIGGER stop_cas")


def test_legacy_writer_cannot_overwrite_existing_generic_cas_policy(connected_core):
    service, session, account, _loan, _state, downloaded = connected_core
    _key, scope = _seed_legacy_on(service, account)
    broker = SynchronizationBrokerImpl(service)
    source_id = broker._source_id(account)
    broker.set_enabled(source_id, False)
    first = read_policy(service.storage, scope)
    from cifereca.core.source_sync_consent import (
        retire_legacy_acquisition_on_source_disable,
    )
    with pytest.raises(ValueError, match="source_sync_consent_changed"):
        retire_legacy_acquisition_on_source_disable(
            service, account, source_enablement_key(scope),
        )
    assert read_policy(service.storage, scope) == first
    assert not downloaded
