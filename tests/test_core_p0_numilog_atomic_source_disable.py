"""#1905: source disable and Numilog acquisition-consent revoke are atomic."""
from __future__ import annotations

import sqlite3

import pytest

from cifereca.core.numilog_materialization_consent import mode, policy_key
from tests.test_core_p0_numilog_auto_materialization import _setup, _authorize
from tests.test_core_p0_numilog_startup_account_isolation import _two_accounts


def test_disable_commits_source_and_consent_revoke_and_never_resurrects(
    tmp_path,
):
    service, account, session, calls, broker, sid = _setup(tmp_path)
    try:
        broker.set_enabled(sid, True)
        pending = broker.prepare_numilog_materialization_consent(sid)
        assert pending.ok is True
        _authorize(broker, sid)
        old_epoch = service.storage.numilog_materialization_consent_revision(sid)
        assert mode(broker, sid) == "on_start"

        broker.set_enabled(sid, False)
        assert service.storage.metadata_get("sync.source.enabled." + sid) == "0"
        assert service.storage.metadata_get(policy_key(sid)) == "manual"
        new_epoch = service.storage.numilog_materialization_consent_revision(sid)
        assert new_epoch != old_epoch
        assert broker.numilog_materialization_schedule(sid) == "manual"
        assert not broker.confirm_numilog_materialization_consent(
            sid, pending.facts["confirmation_token"], mode="on_start",
        ).ok

        broker.set_enabled(sid, True)
        assert broker.numilog_materialization_schedule(sid) == "manual"
        assert service.storage.metadata_get("sync.source.enabled." + sid) == "1"
        assert service.storage.metadata_get(policy_key(sid)) == "manual"
        assert not service.storage.numilog_materialization_change_consent(
            sid, activate=True, expected_revision=old_epoch,
        )
        assert calls == []
    finally:
        service.close()


def test_atomic_disable_rolls_back_all_writes_if_consent_update_fails(tmp_path):
    service, account, session, calls, broker, sid = _setup(tmp_path)
    try:
        broker.set_enabled(sid, True)
        _authorize(broker, sid)
        before = service.storage.numilog_materialization_consent_revision(sid)
        # Simulate storage failure on the *last* of the three writes. The
        # previously written disabled flag and epoch must both roll back.
        with service.storage._connect() as db:
            db.execute(
                "CREATE TRIGGER deny_numilog_disable BEFORE UPDATE OF value "
                "ON metadata WHEN NEW.key='"
                + policy_key(sid)
                + "' BEGIN SELECT RAISE(ABORT, 'injected_failure'); END"
            )
        with pytest.raises(sqlite3.DatabaseError):
            broker.set_enabled(sid, False)
        assert service.storage.metadata_get("sync.source.enabled." + sid) == "1"
        assert service.storage.metadata_get(policy_key(sid)) == "on_start:v1"
        assert service.storage.numilog_materialization_consent_revision(sid) == before
        assert broker.numilog_materialization_schedule(sid) == "on_start"

        with service.storage._connect() as db:
            db.execute("DROP TRIGGER deny_numilog_disable")
        broker.set_enabled(sid, False)
        assert service.storage.metadata_get("sync.source.enabled." + sid) == "0"
        assert service.storage.metadata_get(policy_key(sid)) == "manual"
        assert calls == []
    finally:
        service.close()


def test_disable_one_numilog_account_does_not_change_sibling_consent(tmp_path):
    service, broker, first, second = _two_accounts(tmp_path)
    a, session_a, calls_a, sid_a = first
    b, session_b, calls_b, sid_b = second
    try:
        before = service.storage.numilog_materialization_consent_revision(sid_b)
        broker.set_enabled(sid_a, False)
        assert service.storage.metadata_get(policy_key(sid_a)) == "manual"
        assert service.storage.metadata_get(policy_key(sid_b)) == "on_start:v1"
        assert service.storage.numilog_materialization_consent_revision(sid_b) == before
        assert broker.numilog_materialization_schedule(sid_a) == "manual"
        assert broker.numilog_materialization_schedule(sid_b) == "on_start"
        assert calls_a == calls_b == []
    finally:
        service.close()


def test_disable_numilog_source_rejects_inflight_cross_process_activation(
    tmp_path,
):
    from cifereca.storage import Storage

    service, account, session, calls, broker, sid = _setup(tmp_path)
    replica = Storage(service.storage.path)
    try:
        broker.set_enabled(sid, True)
        _authorize(broker, sid)
        remembered = replica.numilog_materialization_consent_revision(sid)
        broker.set_enabled(sid, False)
        assert replica.metadata_get("sync.source.enabled." + sid) == "0"
        assert replica.metadata_get(policy_key(sid)) == "manual"
        assert not replica.numilog_materialization_change_consent(
            sid, activate=True, expected_revision=remembered,
        )
        assert replica.metadata_get(policy_key(sid)) == "manual"
        assert calls == []
    finally:
        replica.close()
        service.close()


@pytest.mark.parametrize("invalid", ["", "numilog:fake", "sync:ABC", "sync:" + "g"*24])
def test_atomic_disable_rejects_invalid_source_without_any_metadata_change(
    tmp_path, invalid,
):
    service, account, session, calls, broker, sid = _setup(tmp_path)
    try:
        _authorize(broker, sid)
        old_epoch = service.storage.numilog_materialization_consent_revision(sid)
        with pytest.raises(ValueError, match="invalid_numilog_source"):
            service.storage.numilog_materialization_disable_source(invalid)
        assert service.storage.metadata_get(policy_key(sid)) == "on_start:v1"
        assert service.storage.numilog_materialization_consent_revision(sid) == old_epoch
        assert calls == []
    finally:
        service.close()


@pytest.mark.parametrize("disabled_flag", ["0", "corrupt", "-1"])
def test_reenabling_legacy_disabled_source_cannot_resurrect_active_backup(
    tmp_path, disabled_flag,
):
    service, account, session, calls, broker, sid = _setup(tmp_path)
    try:
        broker.set_enabled(sid, True)
        _authorize(broker, sid)
        before = service.storage.numilog_materialization_consent_revision(sid)
        # Simulate the historical non-atomic disable, whose second write
        # never happened: source disabled, backup grant still on_start:v1.
        service.storage.metadata_set("sync.source.enabled." + sid, disabled_flag)
        assert service.storage.metadata_get(policy_key(sid)) == "on_start:v1"

        broker.set_enabled(sid, True)
        assert service.storage.metadata_get("sync.source.enabled." + sid) == "1"
        assert service.storage.metadata_get(policy_key(sid)) == "manual"
        assert service.storage.numilog_materialization_consent_revision(sid) != before
        assert broker.numilog_materialization_schedule(sid) == "manual"
        assert calls == []
    finally:
        service.close()


def test_enabling_already_enabled_source_does_not_cancel_a_confirmed_backup(
    tmp_path,
):
    service, account, session, calls, broker, sid = _setup(tmp_path)
    try:
        broker.set_enabled(sid, True)
        _authorize(broker, sid)
        before = service.storage.numilog_materialization_consent_revision(sid)
        broker.set_enabled(sid, True)
        assert service.storage.metadata_get(policy_key(sid)) == "on_start:v1"
        assert service.storage.numilog_materialization_consent_revision(sid) == before
        assert calls == []
    finally:
        service.close()


def test_legacy_reenable_failure_rolls_back_consent_reset_and_source_switch(
    tmp_path,
):
    service, account, session, calls, broker, sid = _setup(tmp_path)
    try:
        broker.set_enabled(sid, True)
        _authorize(broker, sid)
        service.storage.metadata_set("sync.source.enabled." + sid, "0")
        before = service.storage.numilog_materialization_consent_revision(sid)
        with service.storage._connect() as db:
            db.execute(
                "CREATE TRIGGER deny_legacy_numilog_reenable BEFORE UPDATE OF value "
                "ON metadata WHEN NEW.key='sync.source.enabled."
                + sid +
                "' BEGIN SELECT RAISE(ABORT, 'no_partial_reenable'); END"
            )
        with pytest.raises(sqlite3.DatabaseError):
            broker.set_enabled(sid, True)
        assert service.storage.metadata_get("sync.source.enabled." + sid) == "0"
        assert service.storage.metadata_get(policy_key(sid)) == "on_start:v1"
        assert service.storage.numilog_materialization_consent_revision(sid) == before
        with service.storage._connect() as db:
            db.execute("DROP TRIGGER deny_legacy_numilog_reenable")
        broker.set_enabled(sid, True)
        assert service.storage.metadata_get(policy_key(sid)) == "manual"
        assert calls == []
    finally:
        service.close()
