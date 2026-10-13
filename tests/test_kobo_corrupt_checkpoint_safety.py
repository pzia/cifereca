"""P0: invalid per-source Kobo metadata cannot crash startup diagnostics.

A legacy/partially-written checkpoint must be treated as unknown and
immediately eligible for revalidation, never printed as arbitrary user text.
"""
from __future__ import annotations

import json

from cifereca.core.kobo_backup import _kobo_checkpoint_key
from tests.test_kobo_backup_reconcile_prune import _service


def test_corrupt_checkpoint_stays_private_and_does_not_crash_report_or_preview(tmp_path):
    service = _service(tmp_path, manifest=[])
    key = _kobo_checkpoint_key("default", "vault-1")
    leak = "PRIVATE_TOKEN_OR_BOOK_TITLE"
    service.storage.metadata_set(key, json.dumps({
        "schema_version": 1,
        "status": leak,
        "total": {"secret": leak},
        "processed": "noninteger",
        "updated_at": ["secret", leak],
        "failure_streak": "invalid",
        "counts": {
            "errors": 2,
            "downloaded": leak,
            "credential_id": leak,
            "remaining": True,
        },
    }))
    try:
        diagnostic = service.kobo_backup_diagnostic()
        assert len(diagnostic["sources"]) == 1
        row = diagnostic["sources"][0]
        assert row["status"] == "unknown"
        assert row["total"] == row["position"] == row["updated_at"] == 0
        assert row["failure_streak"] == 0
        assert row["errors"] == 2
        assert "downloaded" not in row
        assert "remaining" not in row
        assert leak not in json.dumps(diagnostic["sources"])
        assert service._kobo_backup_account_due("vault-1")

        # Default preview remains offline and cannot leak malicious local data.
        preview = service.kobo_backup_preview("vault-1")
        assert preview["checkpoint_status"] == "unknown"
        assert preview["manifest_refreshed"] is False
        assert preview["acquisition_authorized"] is False
        assert leak not in json.dumps(preview)
    finally:
        service.close()


def test_checkpoint_numeric_bounds_and_position_never_exceed_total(tmp_path):
    service = _service(tmp_path, manifest=[])
    key = _kobo_checkpoint_key("default", "vault-1")
    try:
        service.storage.metadata_set(key, json.dumps({
            "schema_version": 1, "status": "complete",
            "total": 3, "processed": 999_999, "updated_at": 4_102_444_801,
            "failure_streak": 6,
            "counts": {"scanned": 3},
        }))
        row = service.kobo_backup_diagnostic()["sources"][0]
        assert row["status"] == "complete"
        assert row["total"] == 3
        assert row["position"] == 3
        assert row["updated_at"] == 0
        assert row["failure_streak"] == 6
        assert row["scanned"] == 3

        service.storage.metadata_set(key, json.dumps({
            "schema_version": 1, "status": "manifest_fetch",
            "total": True, "processed": False, "updated_at": 1_800_000_000,
            "failure_streak": 0,
        }))
        row = service.kobo_backup_diagnostic()["sources"][0]
        assert row["status"] == "manifest_fetch"
        assert row["total"] == row["position"] == 0
        assert row["updated_at"] == 1_800_000_000
        assert service._kobo_backup_account_due("vault-1")
    finally:
        service.close()
