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
    account = next(a for a in service.accounts if a.provider_id == "kobo")
    service.storage.metadata_set(policy_key(service.sync_source_id(account)), "1")
    assert [s.credential_id for s in kobo_on_start_sessions(service)] == [
        account.credential_id
    ]
    return service, account


def test_canonical_pending_kobo_does_not_fall_through_to_legacy_get(
    tmp_path, monkeypatch,
):
    service, account = _ready_source(tmp_path)
    calls = []

    def canonical(_service, observed, snapshots, **kwargs):
        if observed.provider_id == "kobo":
            calls.append(("canonical", observed.identity_id, observed.credential_id))
            return {
                field: (1 if field == "pending" else 0)
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
        assert owner["canonical_execution"]["pending"] == 1
        assert calls == [("canonical", account.identity_id, account.credential_id)]
        assert service.storage.metadata_get(
            "sync.source.materialization.last_attempt." +
            service.sync_source_id(account)
        ) is None  # no legacy attempt was recorded
    finally:
        service.close()


def test_old_kobo_session_without_canonical_attempt_keeps_legacy_path(
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
        assert calls == [account.credential_id]
        assert service.storage.metadata_get(
            "sync.source.materialization.last_attempt." +
            service.sync_source_id(account)
        ) is not None
    finally:
        service.close()
