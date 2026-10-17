"""P0 #1346/#1905: one scheduled source exception cannot starve siblings."""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from cifereca.brokers import SynchronizationBrokerImpl
from cifereca.models import Account
from tests.test_core_p0_1346_inventory_refresh_broker import _rig
from tests.test_core_p0_1905_numilog_inventory_schedule import _numilog


@pytest.mark.parametrize("provider", ["kobo", "numilog"])
def test_scheduled_inventory_state_write_exception_is_scoped_to_one_account(
    tmp_path, monkeypatch, provider,
):
    service, _account, _session, calls = (
        _rig(tmp_path) if provider == "kobo" else _numilog(tmp_path)
    )
    try:
        other = Account(
            display_name="Private store second", unique_id="alice-second",
            provider_id=provider, credential_id="vault-second",
            provider_label=provider.title(), identity_id="alice",
        )
        service.accounts.append(other)
        service._sessions[provider + "::vault-second"] = NS(
            provider_id=provider, credential_id="vault-second",
            connected=True, capabilities=NS(personal_library=True, backup=False),
        )
        service.storage.provider_connection_put({
            "identity_id": "alice", "provider_id": provider,
            "credential_id": "vault-second", "enabled": True,
        })
        broker = SynchronizationBrokerImpl(service)
        for account in service.accounts:
            broker.set_inventory_refresh_schedule(
                broker._source_id(account), "on_start", interval_minutes=15,
            )

        first, second = sorted(service.accounts, key=broker._source_id)
        failed_key = "sync.source.state." + broker._source_id(first)
        original = service.storage.metadata_set
        writes = []

        def fail_first_source_only(key, value):
            writes.append(key)
            if key == failed_key:
                raise RuntimeError(
                    "https://private.invalid/fulfillment?auth=SIGNED_SECRET"
                )
            return original(key, value)

        monkeypatch.setattr(service.storage, "metadata_set", fail_first_source_only)
        result = broker.synchronize_scheduled_inventory("alice", "startup")

        assert result.ok is False
        assert result.changed == 1
        assert result.facts == {
            "phase": "discover_rights",
            "trigger": "startup",
            "attempted": 2, "failed": 1,
            "acquisition_authorized": False,
        }
        assert len(calls) == 1
        assert calls[0][:2] == ("alice", second.credential_id)
        assert failed_key in writes
        assert "SIGNED_SECRET" not in repr(result)
        assert broker._scheduled_inventory_running == set()
        assert len(broker._scheduled_inventory_requested) == 2

        # One startup dispatch is consumed even when a source fails: no
        # immediate unbounded retry on the same startup trigger.
        repeated = broker.synchronize_scheduled_inventory("alice", "startup")
        assert repeated.ok and repeated.facts["attempted"] == 0
        assert len(calls) == 1
    finally:
        service.storage.close()
