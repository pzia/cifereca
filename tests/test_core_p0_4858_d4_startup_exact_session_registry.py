"""P0 #4858 D4: startup policy requires the exact registered Kobo session.

A stale, mismatched registry key is not a provider connection, even if the
session values still match an old borrower and the same persisted ON exists.
Synthetic transport, live LibraryService/SQLite, no GET on rejection.
"""
from __future__ import annotations

import pytest

from cifereca.brokers import SynchronizationBrokerImpl
from cifereca.core.source_materialization_schedule import (
    can_enable_kobo_on_start, kobo_on_start_sessions, policy_key,
)
from tests.test_kobo_sync_modes import (
    _service, _enable_on_start, _neutralize_unrelated_sync_work,
)


def test_detached_alias_cannot_run_legacy_startup_or_consume_clock(
    tmp_path, monkeypatch,
):
    service = _service(tmp_path)
    try:
        _neutralize_unrelated_sync_work(service, monkeypatch)
        _enable_on_start(service)
        account = service.accounts[0]
        sid = SynchronizationBrokerImpl._source_id(account)
        clock = "sync.source.materialization.last_attempt." + sid
        # This historical minimal fake uses a tuple key (unlike the real
        # ProviderSession runtime); arbitrary aliases remain forbidden.
        key = ("kobo", account.credential_id)
        session = service._sessions.pop(key)
        service._sessions[("kobo", "stale-alias")] = session
        assert session.connected and session.accounts == [account]
        assert service.storage.metadata_get(policy_key(sid)) == "1"
        assert service.storage.metadata_get(clock) is None
        assert not can_enable_kobo_on_start(service, account)
        assert kobo_on_start_sessions(service) == ()

        monkeypatch.setattr(
            service, "_sync_kobo_backup_account_locked",
            lambda *_a, **_kw: pytest.fail(
                "detached provider alias may not enter legacy W2"
            ),
        )
        result = service._opening_sync_locked(deep=False)
        assert result["kobo_decision"]["scheduled_kobo"] == 0
        assert service.storage.metadata_get(clock) is None
        assert service.storage.metadata_get(policy_key(sid)) == "1"

        # The old preference is historical data, not an implicit new opt-in.
        # Re-registering the *same* actual borrower can recover the existing
        # consent; no policy row is recreated or silently deleted.
        service._sessions.pop(("kobo", "stale-alias"))
        service._sessions[key] = session
        assert kobo_on_start_sessions(service) == (session,)
    finally:
        service.close()


def test_stale_alias_cannot_create_new_legacy_auto_schedule(tmp_path):
    service = _service(tmp_path)
    try:
        account = service.accounts[0]
        service.storage.provider_connection_put({
            "identity_id": account.identity_id,
            "provider_id": "kobo",
            "credential_id": account.credential_id,
            "enabled": True,
        })
        session = service._sessions.pop(
            ("kobo", account.credential_id)
        )
        service._sessions[("kobo", "wrong-credential-index")] = session
        sid = SynchronizationBrokerImpl._source_id(account)
        assert not can_enable_kobo_on_start(service, account)
        with pytest.raises(ValueError):
            SynchronizationBrokerImpl(service).set_local_materialization_schedule(
                sid, "on_start",
            )
        assert service.storage.metadata_get(policy_key(sid)) is None
        assert kobo_on_start_sessions(service) == ()
    finally:
        service.close()
