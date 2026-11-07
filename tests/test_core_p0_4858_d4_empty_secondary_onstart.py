"""P0 #4858 D4: the empty Kobo borrower denotes the primary ONLY.

Legacy on_start is keyed by a normalized account digest. Rebinding from
primary uid="" to secondary uid="" must never reuse an old ON or retry
clock for an automatic provider GET.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from cifereca.brokers import SynchronizationBrokerImpl
from cifereca.core.source_materialization_schedule import (
    can_enable_kobo_on_start, kobo_on_start_sessions, policy_key,
)
from tests.test_kobo_sync_modes import (
    _service, _enable_on_start, _neutralize_unrelated_sync_work,
)


@pytest.mark.parametrize("changed", ("core_account", "session_account"))
def test_old_primary_on_start_cannot_authorize_empty_secondary_borrower(
    tmp_path, monkeypatch, changed,
):
    service = _service(tmp_path)
    try:
        first = replace(service.accounts[0], unique_id="", is_primary=True)
        session = service._sessions[("kobo", "vault")]
        service.accounts = [first]
        session.accounts = [first]
        _neutralize_unrelated_sync_work(service, monkeypatch)
        _enable_on_start(service)
        sid = SynchronizationBrokerImpl._source_id(first)
        clock = "sync.source.materialization.last_attempt." + sid
        assert can_enable_kobo_on_start(service, first)
        assert kobo_on_start_sessions(service) == (session,)
        assert service.storage.metadata_get(policy_key(sid)) == "1"
        assert service.storage.metadata_get(clock) is None

        secondary = replace(first, is_primary=False)
        # The key is identical even though its ownership semantics changed.
        assert SynchronizationBrokerImpl._source_id(secondary) == sid
        if changed == "core_account":
            service.accounts = [secondary]
            session.accounts = [secondary]
            selected = secondary
        else:
            session.accounts = [secondary]
            selected = first

        assert not can_enable_kobo_on_start(service, selected)
        assert kobo_on_start_sessions(service) == ()
        with pytest.raises(ValueError):
            SynchronizationBrokerImpl(service).set_local_materialization_schedule(
                sid, "on_start",
            )
        monkeypatch.setattr(
            service, "_sync_kobo_backup_account_locked",
            lambda *_a, **_kw: pytest.fail(
                "secondary empty Kobo account cannot inherit primary automatic GET"
            ),
        )
        result = service._opening_sync_locked(deep=False)
        assert result["kobo_decision"]["scheduled_kobo"] == 0
        assert service.storage.metadata_get(clock) is None
        assert service.storage.metadata_get(policy_key(sid)) == "1"

        # Historical preference remains intact, and becomes eligible again
        # only after the real primary owner is restored.
        service.accounts = [first]
        session.accounts = [first]
        assert kobo_on_start_sessions(service) == (session,)
    finally:
        service.close()


def test_empty_secondary_cannot_newly_opt_in_even_without_previous_primary(
    tmp_path,
):
    service = _service(tmp_path)
    try:
        secondary = replace(service.accounts[0], unique_id="", is_primary=False)
        service.accounts = [secondary]
        session = service._sessions[("kobo", "vault")]
        session.accounts = [secondary]
        service.storage.provider_connection_put({
            "identity_id": secondary.identity_id,
            "provider_id": "kobo", "credential_id": secondary.credential_id,
            "enabled": True,
        })
        sid = SynchronizationBrokerImpl._source_id(secondary)
        assert not can_enable_kobo_on_start(service, secondary)
        with pytest.raises(ValueError):
            SynchronizationBrokerImpl(service).set_local_materialization_schedule(
                sid, "on_start",
            )
        assert service.storage.metadata_get(policy_key(sid)) is None
        assert kobo_on_start_sessions(service) == ()
    finally:
        service.close()
