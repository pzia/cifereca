"""Kobo P0: externally shareable diagnostics must not contain personal book data.

All data is synthetic; only SQLite + mock provider sessions are used.
"""
from __future__ import annotations

import json

from cifereca.core.kobo_backup import _safe_kobo_backup_diagnostic_detail
from tests.test_kobo_backup_observability import _service


def test_diagnostic_serializer_rejects_untrusted_nested_identifiers_and_messages():
    sensitive = {
        "identity": "PRIVATE_IDENTITY_123",
        "credential": "PRIVATE_KOBO_CREDENTIAL_456",
        "title": "PRIVATE_BOOK_TITLE_789",
        "item": "PRIVATE_ITEM_ID_543",
        "path": "/home/private/Books/license.acsm",
        "url": "https://acquisition.example/file?token=PRIVATE_SIGNED_URL",
        "message": "privatebearertoken",
    }
    summary = {
        "scope": "account", "status": "failed", "aborted": True,
        "cancelled": False, "manifest_items": 522, "attempted": 250,
        "downloaded": 0, "errors": 250,
        "target_dirs": {"default": sensitive["path"]},
        "sources": [
            {
                "identity_id": sensitive["identity"],
                "credential_id": sensitive["credential"],
                "status": "failed", "manifest_items": 522, "attempted": 250,
                "errors": 250, "title": sensitive["title"],
                "item_id": sensitive["item"], "local_path": sensitive["path"],
            }
        ],
        "error_summary": [{
            "phase": "vault", "code": sensitive["message"],
            "status_code": 403, "count": 250,
            "message": sensitive["url"],
        }],
        "abort_reason": {
            "phase": "vault", "code": "unauthorized", "status_code": 401,
            "message": sensitive["message"], "title": sensitive["title"],
            "item_id": sensitive["item"],
        },
        "error_examples": [
            {"title": sensitive["title"], "item_id": sensitive["item"],
             "message": sensitive["url"]}
        ],
        "error_messages": [sensitive["message"]],
    }
    safe = _safe_kobo_backup_diagnostic_detail(summary)
    public = json.dumps(safe, ensure_ascii=False)
    for value in sensitive.values():
        assert value not in public
    for key in ("identity_id", "credential_id", "title", "item_id",
                "local_path", "target_dirs", "error_examples", "error_messages"):
        assert f'"{key}"' not in public
    assert safe["scope"] == "account"
    assert safe["status"] == "failed"
    assert safe["aborted"] is True
    assert safe["attempted"] == 250
    assert safe["sources"] == [{
        "source_index": 1, "status": "failed", "manifest_items": 522,
        "attempted": 250, "errors": 250,
    }]
    assert safe["error_summary"] == [{
        "phase": "vault", "code": "other", "status_code": 403, "count": 250,
    }]
    assert safe["abort_reason"] == {
        "phase": "vault", "code": "unauthorized", "status_code": 401,
    }


def test_real_failed_kobo_backup_keeps_full_in_process_result_but_redacts_diagnostics(tmp_path):
    private_title = "CONFIDENTIAL_KOBO_BOOK_TITLE"
    service, _session = _service(tmp_path, manifest=[{
        "product_id": "CONFIDENTIAL_ITEM_ID", "revision_id": "rev-1",
        "title": private_title, "format": "EPUB", "content_type": "ebook",
        "owned": True, "downloadable": True,
    }], unauthorized=True)
    try:
        summary = service.sync_kobo_backup()
        assert summary["errors"] == 1
        assert summary["aborted"] is True
        assert summary["error_summary"][0]["message"] == "Sauvegarde Kobo impossible"
        assert summary["sources"][0]["credential_id"] == "vault-1"

        diag = service.diagnostics.snapshot()[-1]
        assert diag["operation"] == "backup sync"
        detail = diag["detail"]
        assert detail["status"] == "aborted"
        assert detail["aborted"] is True
        assert detail["errors"] == 1
        assert detail["sources"][0]["source_index"] == 1
        assert detail["sources"][0]["status"] == "aborted"
        assert detail["error_summary"][0]["code"] == "unauthorized"
        assert detail["error_summary"][0]["status_code"] == 401
        public = json.dumps(detail, ensure_ascii=False)
        for secret in (
            "vault-1", "default", "CONFIDENTIAL_ITEM_ID",
            private_title, "Bearer token invalide", "/home/",
            '"identity_id"', '"credential_id"', '"message"',
            '"error_examples"', '"error_messages"',
        ):
            assert secret not in public
    finally:
        service.close()


def test_safe_detail_never_emits_unbounded_numbers_boolean_or_unknown_status():
    safe = _safe_kobo_backup_diagnostic_detail({
        "status": "PRIVATE_STATUS", "scope": "PRIVATE_SCOPE",
        "downloaded": 1_000_001, "errors": -5, "scanned": True,
        "manifests_fetched": 2,
        "sources": [{
            "status": "PRIVATE_STATUS", "attempted": True,
            "downloaded": 10, "identity_id": "PRIVATE_IDENTITY",
        }],
    })
    assert safe["status"] == "unknown"
    assert safe["scope"] == "all"
    assert safe["manifests_fetched"] == 2
    assert "downloaded" not in safe
    assert "errors" not in safe
    assert "scanned" not in safe
    assert safe["sources"] == [{
        "source_index": 1, "status": "unknown", "downloaded": 10,
    }]
