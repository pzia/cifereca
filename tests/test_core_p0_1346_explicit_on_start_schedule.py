"""P0 #1346 — explicit, account-scoped startup consent; never legacy global."""
from __future__ import annotations

from dataclasses import replace
import json
import time
from types import SimpleNamespace as NS

import pytest

from cifereca.brokers import SynchronizationBrokerImpl
from cifereca.plugin_api import SourceMaterializationScheduleBroker
from cifereca.core.kobo_backup import _kobo_checkpoint_key
from cifereca.core.source_materialization_schedule import (
    kobo_on_start_sessions, policy_key, on_start_due, mark_on_start_attempt,
)
from tests.test_kobo_sync_modes import (
    _service, _neutralize_unrelated_sync_work, _enable_on_start,
)


@pytest.mark.parametrize("legacy_enabled", [False, True])
@pytest.mark.parametrize("deep", [False, True])
def test_historical_global_flag_never_authorizes_startup_download(
    tmp_path, monkeypatch, legacy_enabled, deep,
):
    service = _service(tmp_path)
    try:
        _neutralize_unrelated_sync_work(service, monkeypatch)
        service.config.set("kobo_backup.enabled", legacy_enabled)
        monkeypatch.setattr(
            service, "_sync_kobo_backup_locked",
            lambda **_kwargs: pytest.fail("global Kobo batch is forbidden"),
        )
        monkeypatch.setattr(
            service, "_sync_kobo_backup_account_locked",
            lambda *_args, **_kwargs: pytest.fail("no source consent"),
        )
        if deep:
            monkeypatch.setattr(service, "catalog_scan", lambda **_kwargs: {"scanned": 0})
        result = service._opening_sync_locked(deep=deep)
        expected = "manual_only" if legacy_enabled else "disabled"
        assert result["kobo_decision"]["status"] == expected
        assert result["kobo_decision"]["scheduled_kobo"] == 0
        assert result["kobo"] == ({"status": "manual_only", "skipped": True}
                                 if legacy_enabled else None)
        # Durable CLI projection must agree and contain no private values.
        report = service.kobo_backup_diagnostic()["opening_decision"]
        assert report["status"] == expected
        assert report["scheduled_kobo"] == 0
    finally:
        service.close()


def test_source_schedule_api_defaults_manual_and_rejects_implicit_migration(tmp_path):
    service = _service(tmp_path)
    try:
        broker = SynchronizationBrokerImpl(service)
        assert isinstance(broker, SourceMaterializationScheduleBroker)
        source = broker._source_id(service.accounts[0])
        assert broker.local_materialization_schedule(source) == "manual"
        service.config.set("kobo_backup.enabled", True)
        assert broker.local_materialization_schedule(source) == "manual"
        with pytest.raises(ValueError):
            broker.set_local_materialization_schedule(source, "on_start")
        with pytest.raises(ValueError):
            broker.set_local_materialization_schedule(source, "periodic")
        with pytest.raises(ValueError):
            broker.set_local_materialization_schedule(source, True)
        with pytest.raises(ValueError):
            broker.set_local_materialization_schedule("SECRET-URL", "on_start")
        _enable_on_start(service)
        assert broker.local_materialization_schedule(source) == "on_start"
        assert broker.set_local_materialization_schedule(source, "manual") == "manual"
        assert broker.local_materialization_schedule(source) == "manual"
        assert service.storage.metadata_get(policy_key(source)) == "0"
    finally:
        service.close()


@pytest.mark.parametrize("invalid", ["true", "yes", "on_start", "{\"mode\":\"on_start\"}", "1 "])
def test_invalid_persisted_source_policy_fails_closed(tmp_path, invalid):
    service = _service(tmp_path)
    try:
        _enable_on_start(service)
        broker = SynchronizationBrokerImpl(service)
        source = broker._source_id(service.accounts[0])
        service.storage.metadata_set(policy_key(source), invalid)
        assert broker.local_materialization_schedule(source) == "manual"
        assert kobo_on_start_sessions(service) == ()
    finally:
        service.close()


def test_new_source_consent_ignores_legacy_global_cache_and_throttles_empty_result(
    tmp_path, monkeypatch,
):
    service = _service(tmp_path)
    try:
        _neutralize_unrelated_sync_work(service, monkeypatch)
        _enable_on_start(service)
        service.config.set("kobo_backup.enabled", False)
        service.storage.metadata_set("kobo_backup_last_success_at", int(time.time()))
        calls = []
        monkeypatch.setattr(
            service, "_sync_kobo_backup_locked",
            lambda **_kwargs: pytest.fail("global batch must not execute"),
        )
        def scoped(credential, progress=None):
            calls.append(credential)
            # Provider can legitimately return an empty manifest/checkpoint.
            return {"status": "complete", "errors": 0, "sources": []}
        monkeypatch.setattr(service, "_sync_kobo_backup_account_locked", scoped)
        first = service._opening_sync_locked(deep=False)
        assert calls == ["vault"]
        assert first["kobo_decision"]["scheduled_kobo"] == 1
        assert first["kobo"]["synced_accounts"] == 1
        assert first["kobo"]["status"] == "partial"  # no attested source completion
        second = service._opening_sync_locked(deep=False)
        assert calls == ["vault"]  # source-specific attempt clock throttles
        assert second["kobo"]["synced_accounts"] == 0
        assert second["kobo_decision"]["status"] == "partial"
    finally:
        service.close()


def test_disabled_or_rebound_source_cannot_reuse_old_consent(tmp_path, monkeypatch):
    service = _service(tmp_path)
    try:
        _neutralize_unrelated_sync_work(service, monkeypatch)
        _enable_on_start(service)
        broker = SynchronizationBrokerImpl(service)
        source = broker._source_id(service.accounts[0])
        assert len(kobo_on_start_sessions(service)) == 1
        broker.set_enabled(source, False)
        assert kobo_on_start_sessions(service) == ()
        with pytest.raises(ValueError):
            broker.set_local_materialization_schedule(source, "on_start")
        broker.set_enabled(source, True)
        service.storage.provider_connection_put({
            "identity_id": "other", "provider_id": "kobo",
            "credential_id": "vault", "enabled": True,
        })
        assert kobo_on_start_sessions(service) == ()
        assert broker.local_materialization_schedule(source) == "on_start"  # persisted, not authorized
        monkeypatch.setattr(
            service, "_sync_kobo_backup_account_locked",
            lambda *_args, **_kwargs: pytest.fail("stale binding"),
        )
        service.config.set("kobo_backup.enabled", True)
        result = service._opening_sync_locked(deep=False)
        assert result["kobo_decision"]["status"] == "manual_only"
    finally:
        service.close()


def test_ambiguous_kobo_credential_is_not_scheduled(tmp_path):
    service = _service(tmp_path)
    try:
        _enable_on_start(service)
        service.accounts.append(replace(
            service.accounts[0], identity_id="other", unique_id="second-account",
        ))
        assert kobo_on_start_sessions(service) == ()
        broker = SynchronizationBrokerImpl(service)
        with pytest.raises(ValueError):
            broker.set_local_materialization_schedule(
                broker._source_id(service.accounts[0]), "on_start",
            )
    finally:
        service.close()


def test_cancelled_account_checkpoint_is_never_auto_resumed(tmp_path, monkeypatch):
    service = _service(tmp_path)
    try:
        _neutralize_unrelated_sync_work(service, monkeypatch)
        _enable_on_start(service)
        service.storage.metadata_set(
            _kobo_checkpoint_key("default", "vault"),
            json.dumps({"schema_version": 1, "status": "cancelled",
                        "updated_at": int(time.time()) - 86400,
                        "total": 5, "processed": 2, "counts": {"downloaded": 2}}),
        )
        assert not on_start_due(service, service.accounts[0])
        monkeypatch.setattr(
            service, "_sync_kobo_backup_account_locked",
            lambda *_args, **_kwargs: pytest.fail("cancelled source must not restart"),
        )
        result = service._opening_sync_locked(deep=False)
        assert result["kobo"]["synced_accounts"] == 0
        assert result["kobo"]["status"] == "fresh_cache_partial"
    finally:
        service.close()


def test_multiple_identities_are_never_fused_into_one_credential():
    from cifereca.core.source_materialization_schedule import kobo_on_start_sessions
    accounts = [
        NS(identity_id="a", provider_id="kobo", credential_id="c1", unique_id="one"),
        NS(identity_id="b", provider_id="kobo", credential_id="c2", unique_id="two"),
    ]
    sessions = {
        key: NS(provider_id="kobo", credential_id=key, connected=True,
                capabilities=NS(backup=True))
        for key in ("c1", "c2")
    }
    values = {}
    for account in accounts:
        values[policy_key(SynchronizationBrokerImpl._source_id(account))] = "1"
    storage = NS(
        identity_for_connection=lambda provider, credential: {"c1": "a", "c2": "b"}[credential],
        provider_connections=lambda identity_id: [
            {"provider_id": "kobo", "credential_id": "c1" if identity_id == "a" else "c2",
             "enabled": True},
        ],
        metadata_get=lambda key: values.get(key),
    )
    host = NS(accounts=accounts, _sessions=sessions, storage=storage,
              sync_source_enabled=lambda account: True)
    assert {s.credential_id for s in kobo_on_start_sessions(host)} == {"c1", "c2"}
    # A newly rebound account cannot borrow the other identity's authorization.
    accounts[0].identity_id = "b"
    assert {s.credential_id for s in kobo_on_start_sessions(host)} == {"c2"}


@pytest.mark.parametrize("malformed", ["garbage", " True ", "2", "", "false", "0"])
def test_malformed_or_disabled_source_switch_never_enables_automatic_download(
    tmp_path, malformed,
):
    service = _service(tmp_path)
    try:
        _enable_on_start(service)
        source = SynchronizationBrokerImpl._source_id(service.accounts[0])
        service.storage.metadata_set("sync.source.enabled." + source, malformed)
        assert kobo_on_start_sessions(service) == ()
    finally:
        service.close()


def test_failed_attempt_clock_read_or_write_never_launches_automatically(
    tmp_path, monkeypatch,
):
    service = _service(tmp_path)
    try:
        _enable_on_start(service)
        account = service.accounts[0]
        original_get = service.storage.metadata_get
        monkeypatch.setattr(
            service.storage, "metadata_get",
            lambda key: (_ for _ in ()).throw(OSError("private-path"))
            if key.startswith("sync.source.materialization.last_attempt.") else original_get(key),
        )
        assert on_start_due(service, account) is False
        monkeypatch.undo()
        original_set = service.storage.metadata_set
        monkeypatch.setattr(
            service.storage, "metadata_set",
            lambda key, value: (_ for _ in ()).throw(OSError("private-path"))
            if key.startswith("sync.source.materialization.last_attempt.")
            else original_set(key, value),
        )
        assert mark_on_start_attempt(service, account) is False
    finally:
        service.close()


@pytest.mark.parametrize("invalid", ["BAD-CLOCK", "-1", "999999999999999999999", "0x10"])
def test_invalid_persisted_clock_never_triggers_download(tmp_path, invalid):
    service = _service(tmp_path)
    try:
        _enable_on_start(service)
        source = SynchronizationBrokerImpl._source_id(service.accounts[0])
        service.storage.metadata_set(
            "sync.source.materialization.last_attempt." + source, invalid,
        )
        assert on_start_due(service, service.accounts[0]) is False
    finally:
        service.close()


def test_future_attempt_clock_does_not_cause_automatic_retry(tmp_path):
    service = _service(tmp_path)
    try:
        _enable_on_start(service)
        source = SynchronizationBrokerImpl._source_id(service.accounts[0])
        service.storage.metadata_set(
            "sync.source.materialization.last_attempt." + source,
            str(int(time.time()) + 3600),
        )
        assert on_start_due(service, service.accounts[0]) is False
    finally:
        service.close()


def test_three_identities_have_independent_default_manual_and_explicit_on_start():
    """#1346 CI acceptance: opt-in for one Kobo account never covers siblings.

    Synthetic service with three independently bound accounts and no provider
    operations: even when all sessions are connected, no source downloads
    without its own persisted one-value permission.
    """
    identities = ("alice", "bob", "carol")
    credentials = ("vault-a", "vault-b", "vault-c")
    accounts = [
        NS(identity_id=identity, provider_id="kobo", credential_id=credential,
           unique_id="fictional-" + credential)
        for identity, credential in zip(identities, credentials)
    ]
    sessions = {
        credential: NS(provider_id="kobo", credential_id=credential, connected=True,
                       capabilities=NS(backup=True))
        for credential in credentials
    }
    owners = dict(zip(credentials, identities))
    source_ids = [SynchronizationBrokerImpl._source_id(account) for account in accounts]
    policy_values: dict[str, str] = {}
    storage = NS(
        metadata_get=lambda key: policy_values.get(key),
        identity_for_connection=lambda provider, credential: (
            owners[credential] if provider == "kobo" else None
        ),
        provider_connections=lambda identity_id: [{
            "provider_id": "kobo", "credential_id": credential, "enabled": True,
        } for credential, owner in owners.items() if owner == identity_id],
    )
    service = NS(
        accounts=accounts, _sessions=sessions, storage=storage,
        sync_source_enabled=lambda account: True,
    )
    assert kobo_on_start_sessions(service) == ()
    policy_values[policy_key(source_ids[1])] = "1"
    assert [s.credential_id for s in kobo_on_start_sessions(service)] == ["vault-b"]
    policy_values[policy_key(source_ids[2])] = "1"
    assert [s.credential_id for s in kobo_on_start_sessions(service)] == [
        "vault-b", "vault-c",
    ]
    policy_values[policy_key(source_ids[1])] = "0"
    assert [s.credential_id for s in kobo_on_start_sessions(service)] == ["vault-c"]
    # A legacy global bit has no policy key and can never authorize Alice.
    assert "vault-a" not in [s.credential_id for s in kobo_on_start_sessions(service)]
