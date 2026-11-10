"""P0 #4858: retired Kobo W2 startup cannot acquire from legacy consent.

The original #1346 positive scheduler contract was retired. Keep end-to-end
opening safety and exact account/clock non-authority; canonical CAS coverage
belongs to the generic acquisition tests rather than this removed executor.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from cifereca.brokers import SynchronizationBrokerImpl
from cifereca.core import source_materialization_schedule as schedule
from tests.test_kobo_sync_modes import _service, _neutralize_unrelated_sync_work


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
        # The retired global flag affects only the legacy report, not the
        # canonical source opening decision; both remain unconsented.
        expected = "manual_only"
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



@pytest.mark.parametrize("legacy_consent", ["1", "0", "true", "", None])
@pytest.mark.parametrize("retry", ["60", "999999", "", None])
def test_retired_kobo_startup_consent_never_authorizes_dispatch(
    tmp_path, monkeypatch, legacy_consent, retry,
):
    service = _service(tmp_path)
    try:
        _neutralize_unrelated_sync_work(service, monkeypatch)
        account = service.accounts[0]
        source_id = SynchronizationBrokerImpl._source_id(account)
        if legacy_consent is not None:
            service.storage.metadata_set(schedule.policy_key(source_id), legacy_consent)
        if retry is not None:
            service.storage.metadata_set(schedule.retry_interval_key(source_id), retry)
        before_consent = service.storage.metadata_get(schedule.policy_key(source_id))
        before_retry = service.storage.metadata_get(schedule.retry_interval_key(source_id))
        monkeypatch.setattr(
            service, "_sync_kobo_backup_account_locked",
            lambda *_a, **_kw: pytest.fail("retired W2 GET must never run"),
        )
        assert schedule.can_enable_kobo_on_start(service, account) is False
        assert schedule.kobo_on_start_sessions(service) == ()
        assert schedule.on_start_due(service, account) is False
        assert schedule.mark_on_start_attempt(service, account) is False
        assert service.storage.metadata_get(schedule.policy_key(source_id)) == before_consent
        assert service.storage.metadata_get(schedule.retry_interval_key(source_id)) == before_retry
        assert service.storage.metadata_get(
            "sync.source.materialization.last_attempt." + source_id
        ) is None
        opening = service._opening_sync_locked(deep=False)
        assert opening["kobo_decision"]["scheduled_kobo"] == 0
    finally:
        service.close()


@pytest.mark.parametrize("rebound", ["account-swapped", "identity-swapped"])
def test_retired_scheduler_never_admits_rebound_kobo_account(
    tmp_path, monkeypatch, rebound,
):
    service = _service(tmp_path)
    try:
        _neutralize_unrelated_sync_work(service, monkeypatch)
        account = service.accounts[0]
        source_id = SynchronizationBrokerImpl._source_id(account)
        service.storage.metadata_set(schedule.policy_key(source_id), "1")
        if rebound == "account-swapped":
            service.accounts[0] = replace(account, unique_id="different-borrower")
        else:
            service.accounts[0] = replace(account, identity_id="other-identity")
        monkeypatch.setattr(
            service, "_sync_kobo_backup_account_locked",
            lambda *_a, **_kw: pytest.fail("rebound W2 must never GET"),
        )
        assert schedule.kobo_on_start_sessions(service) == ()
        assert schedule.on_start_due(service, service.accounts[0]) is False
        assert service._opening_sync_locked(deep=False)["kobo_decision"]["scheduled_kobo"] == 0
        assert service.storage.metadata_get(schedule.policy_key(source_id)) == "1"
    finally:
        service.close()
