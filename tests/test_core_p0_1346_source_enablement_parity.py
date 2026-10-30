"""#1346 — the same source-enable decision at broker, Core and opening sync.

No network, provider loan/book, download or ADEPT call is permitted after a
corrupt source switch. The missing-preference migration remains enabled only
for remote inventory, and is never Kobo acquisition authorization.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from cifereca.models import Account

from cifereca.brokers import SynchronizationBrokerImpl
from cifereca.core.source_enablement import source_enabled
from cifereca.core import sync_coordination
from tests.test_sync_state_contract import _service
from tests.test_core_p0_1862_numilog_store_mirror import _connect


@pytest.mark.parametrize(("raw", "expected"), [
    (None, True), ("1", True), ("true", True), (" TrUe ", True),
    ("YES", True), ("on", True),
    ("0", False), ("false", False), ("OFF", False),
    ("garbage", False), ("PRIVATE_ACCOUNT", False),
    ("", False), ("on_start", False), ("2", False),
    (True, False), (False, False), (1, False), (0, False),
    (b"1", False), ({"enabled": True}, False), ([], False),
])
def test_shared_decoder_is_read_only_and_fails_closed(raw, expected):
    assert source_enabled(raw) is expected


@pytest.mark.parametrize(("raw", "expected"), [
    (None, True), ("1", True), ("true", True), (" YES ", True),
    ("on", True), ("0", False), ("false", False),
    ("bogus-secret", False), ("ON_START", False), ("", False),
])
def test_service_and_broker_agree_for_same_live_account(tmp_path, raw, expected):
    service = _service(tmp_path)
    try:
        account, session, calls = _connect(service)
        broker = SynchronizationBrokerImpl(service)
        source_id = service.sync_source_id(account)
        assert source_id == broker._source_id(account)
        if raw is not None:
            service.storage.metadata_set("sync.source.enabled." + source_id, raw)
        assert service.sync_source_enabled(account) is expected
        assert broker._enabled(source_id) is expected
        ref = next(row for row in broker.sources("default")
                   if row.provider_id == "numilog")
        assert ref.enabled is expected
        assert calls == []
    finally:
        service.close()


@pytest.mark.parametrize("raw", [None, "1"])
def test_legacy_primary_fallback_collision_denies_core_source_before_remote_io(
    tmp_path, raw,
):
    service = _service(tmp_path)
    try:
        primary = Account(identity_id="default", provider_id="paris",
                          credential_id="card-collision", unique_id="")
        literal = Account(identity_id="default", provider_id="paris",
                          credential_id="card-collision",
                          unique_id="paris:card-collision:primary")
        service.accounts.extend((primary, literal))
        sid = service.sync_source_id(primary)
        assert sid == service.sync_source_id(literal)
        assert sid == SynchronizationBrokerImpl._source_id(primary)
        if raw is not None:
            service.storage.metadata_set("sync.source.enabled." + sid, raw)

        # Both distinct borrowers share the old metadata key. Neither may
        # inherit the same ON/implicit-enabled preference or start remote I/O.
        assert service.sync_source_enabled(primary) is False
        assert service.sync_source_enabled(literal) is False
        broker = SynchronizationBrokerImpl(service)
        with pytest.raises(ValueError, match="ambiguous sync source"):
            broker._find_account(sid)
        for account in (primary, literal):
            result = service._synchronize_account_source_impl(account)
            assert result["status"] == "skipped"
            assert result["reason"] == "source_disabled"

        # No blanket switch/migration: once the conflicting account leaves,
        # the formerly valid legacy primary source retains its own preference.
        service.accounts.remove(literal)
        assert service.sync_source_enabled(primary) is True
    finally:
        service.close()


@pytest.mark.parametrize("raw", ["garbage-secret", "", "2", "On Start",
                                  "false", "OFF", "0"])
def test_disabled_or_corrupt_source_is_not_refreshed_by_any_core_path(
    tmp_path, monkeypatch, raw,
):
    service = _service(tmp_path)
    try:
        account, session, calls = _connect(service)
        broker = SynchronizationBrokerImpl(service)
        source_id = broker._source_id(account)
        service.storage.metadata_set("sync.source.enabled." + source_id, raw)
        monkeypatch.setattr(
            sync_coordination, "acquisition_lock_file",
            lambda: tmp_path / "source-sync-guard.lock",
        )

        def should_not_get_session(*_args, **_kwargs):
            pytest.fail("disabled account must not invoke any provider session")
        monkeypatch.setattr(service, "_session_for_account", should_not_get_session)
        direct = service.synchronize_account_source(account)
        assert direct == {
            "provider": "numilog", "account_id": account.unique_id,
            "status": "skipped", "reason": "source_disabled",
        }
        assert service._synchronize_account_source_impl(account) == direct
        result = broker.synchronize_source(source_id)
        assert result.ok and result.changed == 0
        assert result.facts["skipped"] is True
        assert service.mirror_personal_libraries() == {}
        assert calls == []
        assert not service.storage.catalog_entries()
    finally:
        service.close()


def test_corrupt_switch_stays_off_through_opening_but_another_source_updates(
    tmp_path, monkeypatch,
):
    service = _service(tmp_path)
    try:
        blocked, _session, blocked_calls = _connect(
            service, credential="disabled-private", number=3,
        )
        allowed, _session2, allowed_calls = _connect(
            service, credential="enabled-private", identity="alex", number=1,
        )
        service.storage.metadata_set(
            "sync.source.enabled." + service.sync_source_id(blocked),
            "on_start-SECRET",
        )
        assert not service.sync_source_enabled(blocked)
        assert service.sync_source_enabled(allowed)
        # Ignore unrelated loan and ADEPT work. Those paths are orthogonal to
        # this source switch, and must not be used as permission to download.
        monkeypatch.setattr(service, "_kobo_adapter",
                            lambda: NS(available=lambda: False))
        monkeypatch.setattr(service, "_loans_for_account",
                            lambda *_args, **_kw: [])
        monkeypatch.setattr(service, "_reconcile_identity_adept_inventory",
                            lambda *_args: {"status": "skipped"})
        result = service.opening_sync()
        assert blocked_calls == []
        assert allowed_calls == ["library"]
        assert result["catalog_mirror"]["status"] == "inventory_only"
        assert result["catalog_mirror"]["sources_reported"] == 1
        assert len(service.storage.catalog_entries()) == 1
        assert service.storage.catalog_entries()[0]["identity_id"] == "alex"
        assert all(row["new_acquisitions"] == 0 for row in result["identities"])
    finally:
        service.close()


def test_corrupted_toggle_cannot_be_automatically_reenabled_by_kobo_policy(
    tmp_path,
):
    from tests.test_kobo_sync_modes import _service as _kobo_service, _enable_on_start
    from cifereca.core.source_materialization_schedule import kobo_on_start_sessions

    service = _kobo_service(tmp_path)
    try:
        _enable_on_start(service)
        account = service.accounts[0]
        source_id = service.sync_source_id(account)
        assert len(kobo_on_start_sessions(service)) == 1
        service.storage.metadata_set("sync.source.enabled." + source_id, "garbage")
        assert not service.sync_source_enabled(account)
        assert kobo_on_start_sessions(service) == ()
    finally:
        service.close()
