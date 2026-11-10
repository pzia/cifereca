"""P0 #5054: retired Numilog startup consent remains atomically revocable.

Historical on_start:v1 preferences are NEVER new GET grants; storage must
still retire them safely when a source is disabled or re-enabled.
"""
from __future__ import annotations

import sqlite3

import pytest

from cifereca.brokers import SynchronizationBrokerImpl
from cifereca.core.numilog_materialization_consent import mode, policy_key
from cifereca.storage import Storage
from tests.test_sync_state_contract import _service
from tests.test_core_p0_1862_numilog_store_mirror import _connect


@pytest.fixture
def old_policy(tmp_path):
    service = _service(tmp_path)
    account, session, calls = _connect(service, number=0)
    broker = SynchronizationBrokerImpl(service)
    sid = broker._source_id(account)
    broker.set_enabled(sid, True)
    assert service.storage.numilog_materialization_change_consent(
        sid, activate=True, expected_revision=None,
    )
    try:
        yield service, broker, sid, calls
    finally:
        service.close()


def test_disabling_source_atomically_revokes_old_startup_consent(old_policy):
    service, broker, sid, calls = old_policy
    before = service.storage.numilog_materialization_consent_revision(sid)
    assert mode(broker, sid) == "on_start"
    broker.set_enabled(sid, False)
    assert service.storage.metadata_get("sync.source.enabled." + sid) == "0"
    assert service.storage.metadata_get(policy_key(sid)) == "manual"
    after = service.storage.numilog_materialization_consent_revision(sid)
    assert before != after
    assert not service.storage.numilog_materialization_change_consent(
        sid, activate=True, expected_revision=before,
    )
    assert calls == []


def test_source_disable_rolls_back_on_failed_old_consent_revoke(old_policy):
    service, broker, sid, calls = old_policy
    before = service.storage.numilog_materialization_consent_revision(sid)
    with service.storage._connect() as db:
        db.execute(
            "CREATE TRIGGER deny_legacy_revoke BEFORE UPDATE OF value "
            "ON metadata WHEN NEW.key='" + policy_key(sid) +
            "' BEGIN SELECT RAISE(ABORT, 'failure'); END"
        )
    with pytest.raises(sqlite3.DatabaseError):
        broker.set_enabled(sid, False)
    assert service.storage.metadata_get("sync.source.enabled." + sid) == "1"
    assert service.storage.metadata_get(policy_key(sid)) == "on_start:v1"
    assert service.storage.numilog_materialization_consent_revision(sid) == before
    with service.storage._connect() as db:
        db.execute("DROP TRIGGER deny_legacy_revoke")
    broker.set_enabled(sid, False)
    assert service.storage.metadata_get(policy_key(sid)) == "manual"
    assert calls == []


def test_old_epoch_cannot_reactivate_from_other_process(old_policy):
    service, broker, sid, calls = old_policy
    replica = Storage(service.storage.path)
    try:
        previous = replica.numilog_materialization_consent_revision(sid)
        broker.set_enabled(sid, False)
        assert not replica.numilog_materialization_change_consent(
            sid, activate=True, expected_revision=previous,
        )
        assert replica.metadata_get(policy_key(sid)) == "manual"
        assert calls == []
    finally:
        replica.close()


@pytest.mark.parametrize("invalid", ("", "numilog:fake", "sync:ABC", "sync:" + "g" * 24))
def test_invalid_source_never_changes_historical_consent(old_policy, invalid):
    service, broker, sid, calls = old_policy
    before = service.storage.numilog_materialization_consent_revision(sid)
    with pytest.raises(ValueError, match="invalid_numilog_source"):
        service.storage.numilog_materialization_disable_source(invalid)
    assert service.storage.metadata_get(policy_key(sid)) == "on_start:v1"
    assert service.storage.numilog_materialization_consent_revision(sid) == before
    assert calls == []


@pytest.mark.parametrize("disabled", ("0", "corrupt", "-1"))
def test_reenable_never_restores_historical_grant(old_policy, disabled):
    service, broker, sid, calls = old_policy
    epoch = service.storage.numilog_materialization_consent_revision(sid)
    service.storage.metadata_set("sync.source.enabled." + sid, disabled)
    broker.set_enabled(sid, True)
    assert service.storage.metadata_get(policy_key(sid)) == "manual"
    assert service.storage.numilog_materialization_consent_revision(sid) != epoch
    assert calls == []
