"""P0 Kobo: impossible future timestamps must not make a source forever fresh.

No network or download. Uses the persisted checkpoint and legacy TTL paths.
"""
from __future__ import annotations

import json
import time

from cifereca.core.kobo_backup import _kobo_checkpoint_key
from tests.test_kobo_backup_reconcile_prune import _service


def test_clock_rollback_forces_source_revalidation_but_preserves_cancellation(tmp_path):
    service = _service(tmp_path, manifest=[])
    service.config.set("kobo_backup.interval_minutes", 60)
    key = _kobo_checkpoint_key("default", "vault-1")
    now = int(time.time())
    try:
        for status in ("complete", "failed", "partial"):
            service.storage.metadata_set(key, json.dumps({
                "schema_version": 1, "status": status,
                "updated_at": now + 86400,
                "failure_streak": 6,
            }))
            assert service._kobo_backup_account_due("vault-1"), status

        service.storage.metadata_set(key, json.dumps({
            "schema_version": 1, "status": "attention_required",
            "updated_at": now + 86400, "failure_streak": 6,
        }))
        assert not service._kobo_backup_account_due("vault-1")

        service.storage.metadata_set(key, json.dumps({
            "schema_version": 1, "status": "complete",
            "updated_at": now + 240,
        }))
        assert not service._kobo_backup_account_due("vault-1")
        service.storage.metadata_set(key, json.dumps({
            "schema_version": 1, "status": "cancelled",
            "updated_at": now + 86400,
        }))
        assert not service._kobo_backup_account_due("vault-1")

        # A scoped manual resume after cancellation remains allowed.
        recovered = service.sync_kobo_backup_account("vault-1")
        assert recovered["status"] == "complete"
        assert recovered["attempted"] == recovered["downloaded"] == 0
        assert not service._kobo_backup_account_due("vault-1")
    finally:
        service.close()


def test_legacy_global_cache_timestamp_in_future_does_not_hide_kobo_backup(tmp_path):
    service = _service(tmp_path, manifest=[])
    service.config.set("kobo_backup.interval_minutes", 60)
    now = int(time.time())
    try:
        service.storage.metadata_set("kobo_backup_last_attempt_at", now + 86400)
        assert service._kobo_backup_due()
        assert service._kobo_backup_account_due("vault-1")

        service.storage.metadata_set("kobo_backup_last_attempt_at", now + 120)
        assert not service._kobo_backup_due()
        assert not service._kobo_backup_account_due("vault-1")
    finally:
        service.close()
