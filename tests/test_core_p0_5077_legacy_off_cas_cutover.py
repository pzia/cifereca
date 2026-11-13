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


def test_legacy_revision_failure_rolls_back_inventory_preference(connected_core):
    service, _session, account, _loan, _state, downloaded = connected_core
    from cifereca.core.legacy_source_consent_metadata import _consent_generation_key
    scope = scope_for_account(account)
    pref = source_enablement_key(scope)
    revision = _consent_generation_key(scope)
    assert revision is not None
    # An unconfigured, legacy-free source still rotates the historical epoch
    # before old readers can reuse their previously admitted snapshot.
    with service.storage._lock, service.storage._connect() as db:
        db.execute(
            "CREATE TRIGGER deny_legacy_epoch BEFORE INSERT ON metadata "
            "WHEN NEW.key='" + revision + "' "
            "BEGIN SELECT RAISE(ABORT,'epoch_failure'); END"
        )
    try:
        before = service.storage.metadata_get(pref)
        with pytest.raises(sqlite3.IntegrityError, match="epoch_failure"):
            SynchronizationBrokerImpl(service).set_enabled(
                SynchronizationBrokerImpl._source_id(account), False,
            )
        assert service.storage.metadata_get(pref) == before
        assert read_policy(service.storage, scope).mode == "unset"
        assert not downloaded
    finally:
        with service.storage._lock, service.storage._connect() as db:
            db.execute("DROP TRIGGER deny_legacy_epoch")


def test_stale_legacy_writer_cannot_overwrite_cas_admitted_after_first_read(
    connected_core, monkeypatch,
):
    service, _session, account, _loan, _state, downloaded = connected_core
    import cifereca.core.source_sync_consent as policy_module
    scope = scope_for_account(account)
    pref = source_enablement_key(scope)
    original = policy_module.configured

    def stale_read(target_service, target_account):
        # Simulate another writer committing canonical OFF between the old
        # preflight reader and this toggle's BEGIN IMMEDIATE.
        assert original(target_service, target_account) is False
        from cifereca.core.source_sync_consent import _SCHEMA
        import json
        service.storage.metadata_set(policy_key(scope), json.dumps({
            'schema': _SCHEMA,
            'scope': [scope.identity_id, scope.provider_id,
                      scope.credential_id, scope.account_id],
            'enabled': False,
            'revision': 'a' * 32,
        }))
        return False

    monkeypatch.setattr(policy_module, 'configured', stale_read)
    with pytest.raises(ValueError, match='source_sync_consent_changed'):
        SynchronizationBrokerImpl(service).set_enabled(
            SynchronizationBrokerImpl._source_id(account), True,
        )
    assert service.storage.metadata_get(pref) is None
    assert read_policy(service.storage, scope).mode == 'off'
    assert not downloaded


def test_legacy_inventory_writer_refuses_detached_persisted_account(connected_core):
    service, _session, account, _loan, _state, downloaded = connected_core
    scope = scope_for_account(account)
    pref = source_enablement_key(scope)
    # The in-memory account can remain registered while another process
    # removes its durable binding. The legacy writer must fail closed before
    # it creates an inventory toggle or rotates any historical epoch.
    from cifereca.core.legacy_source_consent_metadata import _consent_generation_key
    epoch_key = _consent_generation_key(scope)
    before_epoch = service.storage.metadata_get(epoch_key)
    with service.storage._lock, service.storage._connect() as db:
        db.execute(
            "DELETE FROM provider_accounts WHERE identity_id=? "
            "AND provider_id=? AND credential_id=? AND account_id=?",
            (scope.identity_id, scope.provider_id,
             scope.credential_id, scope.account_id),
        )
    with pytest.raises(ValueError, match="source_sync_consent_scope_changed"):
        SynchronizationBrokerImpl(service).set_enabled(
            SynchronizationBrokerImpl._source_id(account), False,
        )
    assert service.storage.metadata_get(pref) is None
    assert service.storage.metadata_get(epoch_key) == before_epoch
    assert read_policy(service.storage, scope).mode == "unset"
    assert not downloaded
