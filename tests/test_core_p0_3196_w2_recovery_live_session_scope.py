"""P0 #3196 W2: offline/rebound 0.7 receipts cannot drive recovery writes.

All observations are synthetic and local; no provider GET, ADEPT or downloads.
"""
from __future__ import annotations

import pytest

from tests.test_kobo_backup_reconcile_prune import _service


def _interrupted(credential: str, item: str, *, identity="default", account="account"):
    return {
        "provider_id": "kobo", "credential_id": credential,
        "identity_id": identity, "account_id": account,
        "item_id": item, "revision_id": "legacy-revision",
        "title": "Synthetic legacy item", "format": "EPUB",
        "status": "acquiring", "local_path": None,
        "attempt_count": 1, "job_id": "legacy-attempt",
    }


def test_unscoped_recovery_never_writes_disconnected_or_rebound_receipts(tmp_path, monkeypatch):
    service = _service(tmp_path, manifest=[])
    try:
        original = {
            "active": _interrupted("vault-1", "active"),
            "offline": _interrupted("vault-old", "offline"),
            "rebound": _interrupted("vault-1", "rebound", account="former-account"),
            "foreign": _interrupted("vault-1", "foreign", identity="former-profile"),
        }
        for record in original.values():
            service.storage.backup_put(record)
        before = {
            name: service.storage.backup_get("kobo", record["credential_id"], record["item_id"])
            for name, record in original.items()
        }
        monkeypatch.setattr(
            service, "_execute_kobo_acquisition",
            lambda *_args, **_kwargs: pytest.fail("legacy recovery must not GET"),
        )

        summary = service.sync_kobo_backup()
        assert summary["recovered_interrupted"] == 1
        assert summary["reconcile_pending"] == 0
        active = service.storage.backup_get("kobo", "vault-1", "active")
        assert active["status"] == "pending_acquisition"
        assert active["attention_code"] == "interrupted_acquisition_review"
        for name in ("offline", "rebound", "foreign"):
            receipt = before[name]
            record = original[name]
            assert service.storage.backup_get(
                "kobo", record["credential_id"], record["item_id"]
            ) == receipt

        again = service.sync_kobo_backup()
        assert again["recovered_interrupted"] == 0
        assert again["downloaded"] == 0
    finally:
        service.close()


def test_recovery_requires_one_live_session_per_credential(tmp_path):
    service = _service(tmp_path, manifest=[])
    try:
        record = _interrupted("vault-1", "ambiguous")
        service.storage.backup_put(record)
        before = service.storage.backup_get("kobo", "vault-1", "ambiguous")
        # A duplicated registered object must not count as a distinct
        # provider account or authorize a global 0.7 recovery.
        key = "synthetic-extra-session"
        session = next(iter(service._sessions.values()))
        service._sessions[key] = session
        summary = service.sync_kobo_backup()
        assert summary["recovered_interrupted"] == 0
        assert service.storage.backup_get("kobo", "vault-1", "ambiguous") == before
    finally:
        service.close()
