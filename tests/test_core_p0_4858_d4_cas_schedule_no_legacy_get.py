"""P0 #4858 D4: CAS consent cannot revive the retired Kobo W2 scheduler.

Historical on_start metadata is still readable for rollback, but never grants
a GET, retry clock advance or a new confirmation token. Core's canonical
versioned source consent is covered independently by source-sync tests.
"""
from __future__ import annotations

import json
from dataclasses import replace

import pytest

from cifereca.brokers import SynchronizationBrokerImpl
from cifereca.core.source_materialization_schedule import (
    can_enable_kobo_on_start, kobo_on_start_sessions, policy_key,
    on_start_due, mark_on_start_attempt,
)
from cifereca.core.source_sync_consent import (
    policy_key as canonical_policy_key, read_policy, scope_for_account,
)
from tests.test_kobo_sync_modes import _service, _neutralize_unrelated_sync_work


def _cas(service, enabled):
    scope = scope_for_account(service.accounts[0])
    value = (json.dumps({
        "schema": "cifereca.source-sync-consent/1",
        "scope": [scope.identity_id, scope.provider_id,
                  scope.credential_id, scope.account_id],
        "enabled": enabled,
        "revision": "a" * 32,
    }) if enabled is not None else "corrupt-policy")
    service.storage.metadata_set(canonical_policy_key(scope), value)
    return scope


@pytest.mark.parametrize("mode", ["absent", True, False, None],
                         ids=["unset", "on", "off", "invalid"])
@pytest.mark.parametrize("primary_blank", [False, True])
def test_legacy_on_start_never_authorizes_w2_even_with_cas(
    tmp_path, monkeypatch, mode, primary_blank,
):
    service = _service(tmp_path)
    try:
        _neutralize_unrelated_sync_work(service, monkeypatch)
        if primary_blank:
            account = replace(service.accounts[0], unique_id="")
            service.accounts[0] = account
            service._sessions[("kobo", "vault")].accounts = [account]
        account = service.accounts[0]
        sid = SynchronizationBrokerImpl._source_id(account)
        service.storage.metadata_set(policy_key(sid), "1")
        if mode != "absent":
            scope = _cas(service, mode)
            assert read_policy(service.storage, scope).mode == (
                "invalid" if mode is None else "on" if mode else "off"
            )
        else:
            assert read_policy(service.storage, scope_for_account(account)).mode == "unset"

        calls = []
        monkeypatch.setattr(
            service, "_sync_kobo_backup_account_locked",
            lambda *_args, **_kwargs: calls.append("legacy GET"),
        )
        assert not can_enable_kobo_on_start(service, account)
        assert kobo_on_start_sessions(service) == ()
        assert not on_start_due(service, account)
        assert not mark_on_start_attempt(service, account)
        broker = SynchronizationBrokerImpl(service)
        with pytest.raises(ValueError):
            broker.set_local_materialization_schedule(sid, "on_start")
        result = service._opening_sync_locked(deep=False)
        assert result["kobo_decision"]["scheduled_kobo"] == 0
        assert calls == []
        assert service.storage.metadata_get(policy_key(sid)) == "1"
        assert service.storage.metadata_get(
            "sync.source.materialization.last_attempt." + sid
        ) is None
    finally:
        service.close()


@pytest.mark.parametrize("primary_blank", [False, True])
def test_unreadable_cas_never_falls_back_to_w2(
    tmp_path, monkeypatch, primary_blank,
):
    service = _service(tmp_path)
    try:
        if primary_blank:
            account = replace(service.accounts[0], unique_id="")
            service.accounts[0] = account
            service._sessions[("kobo", "vault")].accounts = [account]
        account = service.accounts[0]
        sid = SynchronizationBrokerImpl._source_id(account)
        service.storage.metadata_set(policy_key(sid), "1")
        exact = canonical_policy_key(scope_for_account(account))
        original = service.storage.metadata_get
        def unreadable(key):
            if key == exact:
                raise RuntimeError("canonical policy unavailable")
            return original(key)
        monkeypatch.setattr(service.storage, "metadata_get", unreadable)
        assert not can_enable_kobo_on_start(service, account)
        assert kobo_on_start_sessions(service) == ()
        assert not on_start_due(service, account)
        assert not mark_on_start_attempt(service, account)
        assert service.storage.metadata_get(policy_key(sid)) == "1"
    finally:
        service.close()
