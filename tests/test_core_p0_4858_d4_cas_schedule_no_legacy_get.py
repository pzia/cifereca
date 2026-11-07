"""P0 #4858 D4: a configured CAS source policy owns automatic Kobo acquisition.

An old v0.7 on_start bit is NOT a second grant when a source-wide canonical
policy has been explicitly configured. No network, ADEPT or legacy GET.
"""
from __future__ import annotations

import json
from dataclasses import replace

import pytest

from cifereca.brokers import SynchronizationBrokerImpl
from cifereca.core.source_materialization_schedule import (
    can_enable_kobo_on_start, kobo_on_start_sessions, policy_key,
)
from cifereca.core.source_sync_consent import (
    policy_key as canonical_policy_key, read_policy, scope_for_account,
)
from tests.test_kobo_sync_modes import (
    _service, _enable_on_start, _neutralize_unrelated_sync_work,
)


def _set_canonical_policy(service, *, enabled: bool | None):
    account = service.accounts[0]
    scope = scope_for_account(account)
    raw = (
        json.dumps({
            "schema": "cifereca.source-sync-consent/1",
            "scope": [
                scope.identity_id, scope.provider_id,
                scope.credential_id, scope.account_id,
            ],
            "enabled": enabled,
            "revision": "a" * 32,
        }) if enabled is not None else "corrupt-policy"
    )
    service.storage.metadata_set(canonical_policy_key(scope), raw)
    return scope


@pytest.mark.parametrize("mode", [True, False, None], ids=["on", "off", "invalid"])
def test_existing_legacy_on_start_is_not_a_second_grant_after_cas_policy(
    tmp_path, monkeypatch, mode,
):
    service = _service(tmp_path)
    try:
        _neutralize_unrelated_sync_work(service, monkeypatch)
        _enable_on_start(service)
        account = service.accounts[0]
        source_id = SynchronizationBrokerImpl._source_id(account)
        assert kobo_on_start_sessions(service) == (service._sessions[("kobo", "vault")],)
        assert service.storage.metadata_get(policy_key(source_id)) == "1"
        assert service.storage.metadata_get(
            "sync.source.materialization.last_attempt." + source_id
        ) is None

        scope = _set_canonical_policy(service, enabled=mode)
        assert read_policy(service.storage, scope).mode == (
            "invalid" if mode is None else "on" if mode else "off"
        )
        assert not can_enable_kobo_on_start(service, account)
        assert kobo_on_start_sessions(service) == ()
        assert service.storage.metadata_get(policy_key(source_id)) == "1"

        # The opening coordinator must NOT enter legacy W2 even though the
        # persisted 0.7 schedule remains "on_start".
        monkeypatch.setattr(
            service, "_sync_kobo_backup_account_locked",
            lambda *_a, **_k: pytest.fail(
                "legacy W2 cannot auto GET under a canonical source policy"
            ),
        )
        result = service._opening_sync_locked(deep=False)
        assert result["kobo_decision"]["scheduled_kobo"] == 0
        assert service.storage.metadata_get(
            "sync.source.materialization.last_attempt." + source_id
        ) is None
        assert service.storage.metadata_get(policy_key(source_id)) == "1"
    finally:
        service.close()


def test_absent_canonical_policy_preserves_exact_legacy_compatibility(tmp_path):
    service = _service(tmp_path)
    try:
        _enable_on_start(service)
        account = service.accounts[0]
        assert read_policy(service.storage, scope_for_account(account)).mode == "unset"
        assert can_enable_kobo_on_start(service, account)
        assert kobo_on_start_sessions(service) == (
            service._sessions[("kobo", "vault")],
        )
    finally:
        service.close()


def test_legacy_on_start_cannot_be_newly_enabled_after_cas_opt_in(tmp_path):
    service = _service(tmp_path)
    try:
        # Bind source as the old schedule would, but do not give an ON bit.
        account = service.accounts[0]
        service.storage.provider_connection_put({
            "identity_id": account.identity_id,
            "provider_id": "kobo", "credential_id": account.credential_id,
            "enabled": True,
        })
        _set_canonical_policy(service, enabled=True)
        broker = SynchronizationBrokerImpl(service)
        sid = broker._source_id(account)
        with pytest.raises(ValueError):
            broker.set_local_materialization_schedule(sid, "on_start")
        assert service.storage.metadata_get(policy_key(sid)) is None
        assert kobo_on_start_sessions(service) == ()
    finally:
        service.close()


def test_unreadable_canonical_policy_cannot_fall_back_to_legacy_schedule(
    tmp_path, monkeypatch,
):
    service = _service(tmp_path)
    try:
        _enable_on_start(service)
        original = service.storage.metadata_get
        exact = canonical_policy_key(scope_for_account(service.accounts[0]))
        def read(key):
            if key == exact:
                raise RuntimeError("unreadable source policy")
            return original(key)
        monkeypatch.setattr(service.storage, "metadata_get", read)
        assert kobo_on_start_sessions(service) == ()
        assert not can_enable_kobo_on_start(service, service.accounts[0])
    finally:
        service.close()


@pytest.mark.parametrize("mode", [True, False, None], ids=["on", "off", "invalid"])
def test_empty_primary_kobo_borrower_cas_vetoes_legacy_on_start(
    tmp_path, monkeypatch, mode,
):
    """The exact empty primary borrower MUST NOT bypass an explicit CAS policy."""
    service = _service(tmp_path)
    try:
        account = replace(service.accounts[0], unique_id="")
        service.accounts[0] = account
        session = service._sessions[("kobo", "vault")]
        session.accounts = [account]
        _neutralize_unrelated_sync_work(service, monkeypatch)
        _enable_on_start(service)
        sid = SynchronizationBrokerImpl._source_id(account)
        clock_key = "sync.source.materialization.last_attempt." + sid

        # The legacy schedule is valid only while the exact CAS is absent.
        assert read_policy(service.storage, scope_for_account(account)).mode == "unset"
        assert can_enable_kobo_on_start(service, account)
        assert kobo_on_start_sessions(service) == (session,)
        assert service.storage.metadata_get(policy_key(sid)) == "1"
        assert service.storage.metadata_get(clock_key) is None

        scope = _set_canonical_policy(service, enabled=mode)
        assert scope.account_id == ""
        assert read_policy(service.storage, scope).mode == (
            "invalid" if mode is None else "on" if mode else "off"
        )
        assert not can_enable_kobo_on_start(service, account)
        assert kobo_on_start_sessions(service) == ()
        # A second explicit legacy ON must also be rejected, not just startup.
        broker = SynchronizationBrokerImpl(service)
        with pytest.raises(ValueError):
            broker.set_local_materialization_schedule(sid, "on_start")

        monkeypatch.setattr(
            service, "_sync_kobo_backup_account_locked",
            lambda *_a, **_k: pytest.fail(
                "empty-primary canonical CAS cannot launch legacy Kobo W2"
            ),
        )
        result = service._opening_sync_locked(deep=False)
        assert result["kobo_decision"]["scheduled_kobo"] == 0
        assert service.storage.metadata_get(clock_key) is None
        # Preserve history for supervised recovery without converting it to a grant.
        assert service.storage.metadata_get(policy_key(sid)) == "1"
    finally:
        service.close()


def test_unreadable_empty_primary_cas_cannot_enable_legacy_on_start(
    tmp_path, monkeypatch,
):
    service = _service(tmp_path)
    try:
        account = replace(service.accounts[0], unique_id="")
        service.accounts[0] = account
        session = service._sessions[("kobo", "vault")]
        session.accounts = [account]
        _enable_on_start(service)
        assert kobo_on_start_sessions(service) == (session,)

        exact_key = canonical_policy_key(scope_for_account(account))
        original_get = service.storage.metadata_get

        def unreadable(key):
            if key == exact_key:
                raise RuntimeError("canonical policy unavailable")
            return original_get(key)

        monkeypatch.setattr(service.storage, "metadata_get", unreadable)
        assert not can_enable_kobo_on_start(service, account)
        assert kobo_on_start_sessions(service) == ()
        assert service.storage.metadata_get(
            policy_key(SynchronizationBrokerImpl._source_id(account))
        ) == "1"
    finally:
        service.close()
