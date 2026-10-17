"""P0 #1346/#1905: one scheduled source exception cannot starve siblings."""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from cifereca.brokers import SynchronizationBrokerImpl
from cifereca.models import Account
from cifereca.plugin_api import Cancelled, SyncResult
from tests.test_core_p0_1905_numilog_inventory_schedule import _numilog


@pytest.mark.parametrize("second_provider", ["kobo", "numilog"])
def test_scheduled_inventory_state_write_exception_is_scoped_to_one_account(
    tmp_path, monkeypatch, second_provider,
):
    # The storage model intentionally permits only one Kobo account per
    # identity. Exercise legal Numilog+Kobo and Numilog+Numilog batches.
    service, _account, _session, calls = _numilog(tmp_path)
    try:
        # _numilog starts from a Kobo test rig and mutates its live account
        # into Numilog; its synthetic SQLite Kobo binding remains behind.
        # Remove that fixture-only orphan, not the production uniqueness rule.
        with service.storage._lock, service.storage._connect() as db:
            stale = db.execute(
                "SELECT credential_id FROM provider_connections WHERE "
                "identity_id=? AND provider_id=? AND credential_id=?",
                ("alice", "kobo", "vault-alice"),
            ).fetchall()
            assert len(stale) == 1
            db.execute(
                "DELETE FROM provider_connections WHERE identity_id=? "
                "AND provider_id=? AND credential_id=?",
                ("alice", "kobo", "vault-alice"),
            )
        other = Account(
            display_name="Private store second", unique_id="alice-second",
            provider_id=second_provider, credential_id="vault-second",
            provider_label=second_provider.title(), identity_id="alice",
        )
        service.accounts.append(other)
        service._sessions[second_provider + "::vault-second"] = NS(
            provider_id=second_provider, credential_id="vault-second",
            connected=True, capabilities=NS(personal_library=True, backup=False),
        )
        service.storage.provider_connection_put({
            "identity_id": "alice", "provider_id": second_provider,
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


class _CancelControl:
    def __init__(self):
        self.stopped = False

    def cancel(self):
        self.stopped = True

    def check_cancelled(self):
        if self.stopped:
            raise Cancelled("task cancelled")


def test_cancelled_before_dispatch_never_starts_scheduled_inventory(tmp_path):
    service, account, _session, calls = _numilog(tmp_path)
    try:
        broker = SynchronizationBrokerImpl(service)
        broker.set_inventory_refresh_schedule(broker._source_id(account), "on_start")
        control = _CancelControl()
        control.cancel()
        with pytest.raises(Cancelled):
            broker.synchronize_scheduled_inventory("alice", "startup", control=control)
        assert calls == []
        assert broker._scheduled_inventory_requested == set()
        assert broker._scheduled_inventory_running == set()
    finally:
        service.storage.close()


@pytest.mark.parametrize("failure", ["raised_cancelled", "returned_after_cancel"])
def test_cancellation_is_not_swallowed_or_followed_by_sibling_refresh(
    tmp_path, monkeypatch, failure,
):
    service, account, _session, calls = _numilog(tmp_path)
    try:
        second = Account(
            display_name="Numilog second", unique_id="second-account",
            provider_id="numilog", credential_id="vault-other",
            provider_label="Numilog", identity_id="alice",
        )
        service.accounts.append(second)
        service._sessions["numilog::vault-other"] = NS(
            provider_id="numilog", credential_id="vault-other",
            connected=True, capabilities=NS(personal_library=True, backup=False),
        )
        service.storage.provider_connection_put({
            "identity_id": "alice", "provider_id": "numilog",
            "credential_id": "vault-other", "enabled": True,
        })
        broker = SynchronizationBrokerImpl(service)
        for current in (account, second):
            broker.set_inventory_refresh_schedule(
                broker._source_id(current), "on_start",
            )

        visited = []
        def interrupt(self, source_id, request=None, control=None):
            visited.append(source_id)
            if failure == "raised_cancelled":
                raise Cancelled("cancelled by user")
            control.cancel()
            return SyncResult(ok=True, changed=1)

        monkeypatch.setattr(SynchronizationBrokerImpl, "synchronize_source", interrupt)
        control = _CancelControl()
        with pytest.raises(Cancelled):
            broker.synchronize_scheduled_inventory("alice", "startup", control=control)
        assert len(visited) == 1
        assert broker._scheduled_inventory_running == set()
        assert len(broker._scheduled_inventory_requested) == 1
        assert calls == []
    finally:
        service.storage.close()
