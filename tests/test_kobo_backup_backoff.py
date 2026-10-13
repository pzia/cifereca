"""Kobo P0: durable source-local cooldown never suppresses manual recovery.

The scheduler reads persisted v1 checkpoints; no remote HTTP/download is used.
"""
from __future__ import annotations

import json
import time

from cifereca.core.kobo_backup import _kobo_checkpoint_key
from cifereca.kobo.vault import KoboVaultError
from tests.test_kobo_backup_reconcile_prune import _service


def _checkpoint(service, credential="vault-1", identity="default"):
    key = _kobo_checkpoint_key(identity, credential)
    return key, json.loads(service.storage.metadata_get(key))


def _backdate(service, seconds, credential="vault-1", identity="default"):
    key, row = _checkpoint(service, credential, identity)
    row["updated_at"] = int(time.time()) - seconds
    service.storage.metadata_set(key, json.dumps(row))


def test_failed_source_backoff_survives_sqlite_restart_and_manual_resume(tmp_path):
    outage = KoboVaultError("unavailable", code="temporary", status_code=503)
    service = _service(tmp_path, manifest_error=outage)
    service.config.set("kobo_backup.interval_minutes", 10)
    try:
        first = service.sync_kobo_backup_account("vault-1")
        assert first["status"] == "failed"
        assert first["errors"] == 1
        assert _checkpoint(service)[1]["failure_streak"] == 1
        assert not service._kobo_backup_account_due("vault-1")

        _backdate(service, 11 * 60)
        assert service._kobo_backup_account_due("vault-1")
        second = service.sync_kobo_backup_account("vault-1")
        assert second["status"] == "failed"
        assert _checkpoint(service)[1]["failure_streak"] == 2
        _backdate(service, 11 * 60)
        assert not service._kobo_backup_account_due("vault-1")
    finally:
        service.close()

    # The second failure remains in the same SQLite metadata across restart.
    service = _service(tmp_path, manifest=[])
    service.config.set("kobo_backup.interval_minutes", 10)
    try:
        assert _checkpoint(service)[1]["failure_streak"] == 2
        assert not service._kobo_backup_account_due("vault-1")
        _backdate(service, 21 * 60)
        assert service._kobo_backup_account_due("vault-1")
        # A manual scoped action is never blocked by the auto-scheduler:
        # the account has now recovered and has a valid empty manifest.
        success = service.sync_kobo_backup_account("vault-1")
        assert success["status"] == "complete"
        assert _checkpoint(service)[1]["failure_streak"] == 0
        _backdate(service, 11 * 60)
        assert service._kobo_backup_account_due("vault-1")
    finally:
        service.close()


def test_manual_recovery_ignores_active_backoff_window(tmp_path):
    service = _service(
        tmp_path,
        manifest_error=KoboVaultError("temporary", code="temporary", status_code=503),
    )
    service.config.set("kobo_backup.interval_minutes", 10)
    try:
        assert service.sync_kobo_backup_account("vault-1")["status"] == "failed"
        assert service.sync_kobo_backup_account("vault-1")["status"] == "failed"
        assert _checkpoint(service)[1]["failure_streak"] == 2
        assert not service._kobo_backup_account_due("vault-1")
        public_sources = service.kobo_backup_diagnostic()["sources"]
        assert len(public_sources) == 1
        assert public_sources[0]["failure_streak"] == 2
        assert "vault-1" not in json.dumps(public_sources)
        session = service._sessions[service._session_key("kobo", "vault-1")]
        session._manifest_error = None
        assert service.sync_kobo_backup_account("vault-1")["status"] == "complete"
        assert _checkpoint(service)[1]["failure_streak"] == 0
    finally:
        service.close()


def test_interruption_retries_immediately_and_cancellation_does_not(tmp_path):
    service = _service(tmp_path, manifest=[])
    service.config.set("kobo_backup.interval_minutes", 10)
    key = _kobo_checkpoint_key("default", "vault-1")
    try:
        for status, due in (
            ("running", True), ("interrupted", True),
            ("manifest_fetch", True), ("cancelled", False),
            ("failed", False), ("partial", False),
        ):
            service.storage.metadata_set(key, json.dumps({
                "schema_version": 1, "status": status,
                "updated_at": int(time.time()),
                "failure_streak": 6,
            }))
            assert service._kobo_backup_account_due("vault-1") is due, status
    finally:
        service.close()


def test_legacy_checkpoints_and_global_cache_remain_compatible(tmp_path):
    service = _service(tmp_path, manifest=[])
    service.config.set("kobo_backup.interval_minutes", 10)
    try:
        # Before the first per-account checkpoint, legacy global age still works.
        service.storage.metadata_set("kobo_backup_last_attempt_at", int(time.time()))
        assert not service._kobo_backup_due()
        assert not service._kobo_backup_account_due("vault-1")
        key = _kobo_checkpoint_key("default", "vault-1")
        service.storage.metadata_set(key, json.dumps({
            "schema_version": 1, "status": "failed",
            "updated_at": int(time.time()) - 11 * 60,
            # Old v1 data has no failure_streak: retain the configured TTL.
        }))
        assert service._kobo_backup_account_due("vault-1")
    finally:
        service.close()
