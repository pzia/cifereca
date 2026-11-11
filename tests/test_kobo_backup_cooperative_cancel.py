"""Kobo P0 cooperative cancellation: durable, idempotent, non-destructive."""
from __future__ import annotations

import json

import pytest
from pathlib import Path
from types import SimpleNamespace

from cifereca.core.kobo_backup import _kobo_checkpoint_key
from tests.test_kobo_backup_fault_isolation import (
    _ready_adept_state,
    _service,
    _write_epub,
)


def _manifest():
    return [
        {"product_id": f"item-{i}", "revision_id": "r1",
         "title": f"Book {i}", "format": "EPUB"}
        for i in range(1, 4)
    ]


def _fake_acquirer(calls):
    def acquire(item, target_dir):
        calls.append(item.record_id)
        artifact = Path(target_dir) / f"{item.record_id}.epub"
        _write_epub(artifact)
        return SimpleNamespace(path=artifact, account_id="account", metadata={})
    return acquire



def test_scoped_legacy_resume_is_physically_absent():
    from cifereca.services import LibraryService
    assert not hasattr(LibraryService, "sync_kobo_backup_account")


def test_expired_cancelled_checkpoint_stays_manual_only(tmp_path):
    import time

    service, _session = _service(tmp_path, _manifest())
    try:
        now = int(time.time())
        service.storage.metadata_set(
            _kobo_checkpoint_key("default", "vault-1"),
            json.dumps({"schema_version": 1, "status": "cancelled",
                        "processed": 1, "total": 3, "updated_at": now - 86400}),
        )
        assert not hasattr(service, "_kobo_backup_account_due")
        # A crash must behave differently from a deliberate cancellation.
        service.storage.metadata_set(
            _kobo_checkpoint_key("default", "vault-1"),
            json.dumps({"schema_version": 1, "status": "interrupted",
                        "processed": 1, "total": 3, "updated_at": now}),
        )
        # #1365 supplies the interruption policy; the assertion here
        # prevents treating this diagnostic state as a user cancellation.
        row = service.kobo_backup_diagnostic()["sources"][0]
        assert row["status"] == "interrupted"
    finally:
        service.close()


