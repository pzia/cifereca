"""Kobo P0: a crash while fetching rights cannot leave a false fresh checkpoint.

No network calls or content transfers. Tests use real SQLite metadata and the
existing single account-scoped checkpoint contract.
"""
from __future__ import annotations

import json
import time

import pytest

from cifereca.core.kobo_backup import _kobo_checkpoint_key
from cifereca.kobo.vault import KoboVaultError
from tests.test_kobo_backup_reconcile_prune import _service


def _checkpoint(service):
    return json.loads(service.storage.metadata_get(
        _kobo_checkpoint_key("default", "vault-1")
    ))


def test_manifest_hard_interruption_immediately_resumable_despite_fresh_old_success(
    tmp_path, monkeypatch,
):
    service = _service(tmp_path, manifest=[])
    service.config.set("kobo_backup.interval_minutes", 60)
    key = _kobo_checkpoint_key("default", "vault-1")
    now = int(time.time())
    service.storage.metadata_set(key, json.dumps({
        "schema_version": 1, "status": "complete",
        "updated_at": now, "processed": 30, "total": 30,
        "failure_streak": 0,
    }))
    service.storage.metadata_set("kobo_backup_last_success_at", now)
    assert not service._kobo_backup_account_due("vault-1")
    session = service._sessions[service._session_key("kobo", "vault-1")]
    seen = []

    def abrupt_stop():
        # Checkpoint must be durable *before* provider I/O begins.
        running = _checkpoint(service)
        seen.append((running["status"], running["processed"], running["total"]))
        raise KeyboardInterrupt("simulated hard stop during manifest request")

    monkeypatch.setattr(session, "backup_manifest", abrupt_stop)
    try:
        with pytest.raises(KeyboardInterrupt):
            service.sync_kobo_backup_account("vault-1")
        assert seen == [("manifest_fetch", 0, 0)]
        assert _checkpoint(service)["status"] == "manifest_fetch"
        assert _checkpoint(service)["failure_streak"] == 0
        assert service.storage.metadata_get("kobo_backup_last_success_at") == now
        assert service._kobo_backup_account_due("vault-1")
    finally:
        service.close()

    # Restart without waiting for the global success TTL: a new fetch is
    # permitted and only a real completed source resets its checkpoint.
    restarted = _service(tmp_path, manifest=[])
    restarted.config.set("kobo_backup.interval_minutes", 60)
    try:
        assert restarted._kobo_backup_account_due("vault-1")
        result = restarted.sync_kobo_backup_account("vault-1")
        assert result["status"] == "complete"
        assert result["attempted"] == result["downloaded"] == 0
        assert _checkpoint(restarted)["status"] == "complete"
        assert not restarted._kobo_backup_account_due("vault-1")
    finally:
        restarted.close()


def test_manifest_provider_error_preserves_existing_backoff_and_authority(tmp_path):
    service = _service(
        tmp_path, manifest_error=KoboVaultError(
            "offline", code="temporary", status_code=503,
        ),
    )
    service.config.set("kobo_backup.interval_minutes", 10)
    key = _kobo_checkpoint_key("default", "vault-1")
    service.storage.metadata_set(key, json.dumps({
        "schema_version": 1, "status": "failed",
        "updated_at": int(time.time()) - 40 * 60,
        "failure_streak": 2,
    }))
    service.storage.backup_put({
        "provider_id": "kobo", "credential_id": "vault-1",
        "identity_id": "default", "item_id": "historical",
        "title": "Previously owned", "status": "pending",
    })
    try:
        assert service._kobo_backup_account_due("vault-1")
        result = service.sync_kobo_backup_account("vault-1")
        assert result["status"] == "failed"
        assert result["errors"] == 1
        assert result["manifests_fetched"] == 0
        assert result["legacy_pruned"] == 0
        assert _checkpoint(service)["status"] == "failed"
        assert _checkpoint(service)["failure_streak"] == 3
        assert not service._kobo_backup_account_due("vault-1")
        assert service.storage.backup_get("kobo", "vault-1", "historical") is not None
    finally:
        service.close()
