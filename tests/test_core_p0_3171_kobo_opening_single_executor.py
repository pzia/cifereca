"""#3171 P0: Kobo opening must choose either canonical A→B or legacy, never both.

Real LibraryService + SQLite and a connected synthetic Kobo source. No HTTP.
"""
from __future__ import annotations

import pytest

from cifereca.core import sync_coordination
from cifereca.core.source_materialization_schedule import (
    kobo_on_start_sessions, policy_key,
)
from tests.test_kobo_backup_guard import _service


def _ready_source(tmp_path):
    service = _service(tmp_path)
    # The fixture connects its fake provider but does not synchronize the
    # LibraryService account registry; opening Sync uses the latter.
    session = service._sessions[service._session_key("kobo", "vault-1")]
    account = session.accounts[0]
    service.accounts = [account]
    service.storage.metadata_set(policy_key(service.sync_source_id(account)), "1")
    assert kobo_on_start_sessions(service) == ()
    return service, account


@pytest.mark.parametrize("canonical_pending", (False, True))
def test_canonical_kobo_does_not_fall_through_to_legacy_get(
    tmp_path, monkeypatch, canonical_pending,
):
    service, account = _ready_source(tmp_path)
    calls = []

    def canonical(_service, observed, snapshots, **kwargs):
        if observed.provider_id == "kobo":
            calls.append(("canonical", observed.identity_id, observed.credential_id))
            return {
                field: (1 if field == "pending" and canonical_pending else 0)
                for field in (
                    "present_attested", "downloaded", "recovered", "pending",
                    "blocked", "failed", "cancelled", "skipped_archived", "inactive",
                )
            }
        return None

    def legacy(*_args, **_kwargs):
        pytest.fail("canonical Kobo source must not start a second legacy GET")

    monkeypatch.setattr(sync_coordination, "_run_observed_canonical_convergence", canonical)
    monkeypatch.setattr(service, "_sync_kobo_backup_account_locked", legacy)
    try:
        result = service.opening_sync()
        owner = next(
            row for row in result["identities"] if row["identity_id"] == "alice"
        )
        assert owner["canonical_execution"]["pending"] == int(canonical_pending)
        assert result["kobo"]["status"] == (
            "partial" if canonical_pending else "complete"
        )
        assert result["kobo"]["canonical_sources"] == 1
        assert result["kobo"]["synced_accounts"] == 0
        assert calls == [("canonical", account.identity_id, account.credential_id)]
        assert service.storage.metadata_get(
            "sync.source.materialization.last_attempt." +
            service.sync_source_id(account)
        ) is None  # no legacy attempt was recorded
    finally:
        service.close()


def test_old_kobo_session_without_canonical_attempt_never_runs_legacy(
    tmp_path, monkeypatch,
):
    service, account = _ready_source(tmp_path)
    calls = []
    monkeypatch.setattr(
        sync_coordination, "_run_observed_canonical_convergence",
        lambda *_args, **_kwargs: None,
    )

    def legacy(credential, *, progress=None):
        calls.append(credential)
        return {
            "status": "complete", "errors": 0, "downloaded": 0,
            "sources": [{"status": "complete", "source_id": account.credential_id}],
        }

    monkeypatch.setattr(service, "_sync_kobo_backup_account_locked", legacy)
    try:
        service.opening_sync()
        assert calls == []
        assert service.storage.metadata_get(
            "sync.source.materialization.last_attempt." +
            service.sync_source_id(account)
        ) is None
    finally:
        service.close()


@pytest.mark.parametrize("contract", ("downloadable_v2", "rights_v1"))
def test_on_start_policy_never_schedules_complete_canonical_kobo_session(
    tmp_path, monkeypatch, contract,
):
    """An old ON remains stored but cannot dispatch a second legacy GET."""
    from cifereca.brokers import SynchronizationBrokerImpl

    service, account = _ready_source(tmp_path)
    session = service._sessions[service._session_key("kobo", "vault-1")]
    source_id = service.sync_source_id(account)
    original_policy = service.storage.metadata_get(policy_key(source_id))
    methods = (
        ("downloadable_resource_snapshot", "downloadable_selected_offer_snapshot",
         "downloadable_selected_delivery_candidate")
        if contract == "downloadable_v2" else
        ("rights_observation_snapshot", "rights_selected_delivery_offer_snapshot",
         "rights_selected_delivery_candidate")
    )
    try:
        for name in methods:
            monkeypatch.setattr(
                session, name, lambda *_args, **_kwargs: pytest.fail(
                    "policy must inspect ABI statically"
                ), raising=False,
            )
        assert kobo_on_start_sessions(service) == ()
        assert service.storage.metadata_get(policy_key(source_id)) == original_policy
        broker = SynchronizationBrokerImpl(service)
        with pytest.raises(ValueError):
            broker.set_local_materialization_schedule(source_id, "on_start")

        # A missing/partial source snapshot does not grant a fallback to
        # a historical backup writer merely because an ON was persisted.
        # Canonical observation is independent of this scheduler under test.
        from cifereca.core import canonical_sync_stage
        monkeypatch.setattr(
            canonical_sync_stage, "observe_canonical_rights_in_sync",
            lambda *_args, **_kwargs: None,
        )
        monkeypatch.setattr(
            sync_coordination, "_run_observed_canonical_convergence",
            lambda *_args, **_kwargs: None,
        )
        monkeypatch.setattr(
            service, "_sync_kobo_backup_account_locked",
            lambda *_args, **_kwargs: pytest.fail(
                "canonical Kobo ABI must not launch legacy on_start"
            ),
        )
        service.opening_sync()
        assert service.storage.metadata_get(
            "sync.source.materialization.last_attempt." + source_id
        ) is None
    finally:
        service.close()


def test_on_start_partial_abi_never_reenables_older_kobo_worker(tmp_path, monkeypatch):
    service, account = _ready_source(tmp_path)
    session = service._sessions[service._session_key("kobo", "vault-1")]
    try:
        monkeypatch.setattr(
            session, "downloadable_resource_snapshot",
            lambda *_args, **_kwargs: pytest.fail(
                "an incomplete ABI is not a provider request"
            ), raising=False,
        )
        assert kobo_on_start_sessions(service) == ()
        assert service.storage.metadata_get(
            policy_key(service.sync_source_id(account))
        ) == "1"
    finally:
        service.close()
