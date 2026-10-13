"""Kobo P0: shareable CLI reports never export local paths or provider secrets.

These tests exercise the raw SQLite-backed diagnostics and both CLI aliases.
The local UI API stays detailed; only copyable CLI output is restricted.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from cifereca import cli
from cifereca.core.kobo_backup import safe_kobo_backup_report, _kobo_checkpoint_key
from tests.test_kobo_backup_reconcile_prune import _service


@pytest.mark.parametrize("command", ["kobo-sync-errors", "kobo-backup-errors"])
def test_cli_redacts_persisted_private_book_and_provider_details(
    tmp_path, monkeypatch, capsys, command,
):
    service = _service(tmp_path, manifest=[])
    credential = "vault-1"
    title = "SECRET_PERSONAL_PURCHASE"
    item_id = "PRIVATE_PRODUCT_ID_654"
    path = "/home/private-user/EBooks/PRIVATE_BOOK.epub"
    error = "Bearer SECRET_TOKEN_123 from https://vault.example/download?sig=SECRET_SIGNATURE"
    service.storage.backup_put({
        "provider_id": "kobo", "credential_id": credential,
        "identity_id": "default", "item_id": item_id,
        "title": title, "status": "error", "error": error,
        "local_path": path,
    })
    service.storage.backup_put({
        "provider_id": "kobo", "credential_id": credential,
        "identity_id": "default", "item_id": "reconcile-secret",
        "title": "RECONCILIATION_TITLE", "status": "reconcile_required",
        "error": error, "local_path": path,
    })
    service.storage.backup_put({
        "provider_id": "kobo", "credential_id": credential,
        "identity_id": "default", "item_id": "blocked-secret",
        "title": "BLOCKED_TITLE", "status": "attention_required",
        "attention_code": "missing_delegation_scope",
        "error": error,
    })
    service.storage.metadata_set(_kobo_checkpoint_key("default", credential), json.dumps({
        "schema_version": 1, "status": "partial", "total": 3,
        "processed": 2, "updated_at": 123456789,
        "counts": {"attempted": 1, "errors": 1, "remaining": 1},
        "failure_streak": 2,
    }))

    # Detailed data must remain available to local reconciliation, but must
    # not be printed through the CLI or exported to support tickets.
    detailed = service.kobo_backup_diagnostic()
    assert detailed["error_summary"][0]["message"] == error
    assert detailed["error_examples"][0]["item_id"] == item_id
    assert detailed["reconcile_examples"][0]["local_path"] == path

    def no_network(*_args, **_kwargs):
        pytest.fail("a local diagnostic must never connect to providers")

    monkeypatch.setattr(service, "connect_configured", no_network)
    monkeypatch.setattr(service, "sync_kobo_backup", no_network)
    monkeypatch.setattr(cli.CiferecaCore, "open", lambda **_kw: SimpleNamespace(
        library=service, close=lambda: None,
    ))
    try:
        assert cli.main([command]) == 0
        output = capsys.readouterr().out
        data = json.loads(output)
        assert data["redacted"] is True
        assert data["items"] == 3
        assert data["statuses"] == {
            "error": 1, "reconcile_required": 1, "attention_required": 1,
        }
        assert data["error_count"] == 1
        assert data["reconcile_count"] == 1
        assert data["attention_count"] == 1
        assert data["items_blocked_by_scope"] == 1
        assert len(data["sources"]) == 1
        assert data["sources"][0]["status"] == "partial"
        assert data["sources"][0]["remaining"] == 1
        assert data["sources"][0]["failure_streak"] == 2
        assert data["sources"][0]["updated_at"] == 123456789
        for private in (
            credential, title, item_id, path, error, "PRIVATE_BOOK",
            "RECONCILIATION_TITLE", "BLOCKED_TITLE", "SECRET_SIGNATURE",
            "SECRET_TOKEN_123", "credential_id", "error_examples",
            "reconcile_examples", "attention_examples",
        ):
            assert private not in output
        # In-memory diagnostic is not mutated by creating a shareable view.
        assert service.kobo_backup_diagnostic()["error_summary"][0]["message"] == error
    finally:
        service.close()


def test_export_allowlist_rejects_untrusted_statuses_and_source_metadata():
    report = {
        "items": 12, "items_blocked_by_scope": 4,
        "statuses": {
            "error": 2, "complete": 3,
            "https://vault.example/file?bearer=PRIVATE_SECRET": 1,
            "attention_required": True,
            "reconcile_required": 4,
            "malicious status PRIVATE_TITLE": 2,
            "pending": -4,
        },
        "sources": [
            {
                "source_id": "PRIVATE_CREDENTIAL",
                "credential_id": "PRIVATE_CREDENTIAL",
                "status": "PRIVATE_STATUS",
                "attempted": 3, "errors": 1,
                "remaining": "PRIVATE_REMAINING",
                "failure_streak": 4,
                "total": 3, "updated_at": 12,
                "message": "https://example.invalid/?access_token=PRIVATE_TOKEN",
            },
            {
                "source_id": "0" * 24,
                "status": "failed",
                "downloaded": 0,
                "total": 2,
                "processed": 1,
                "updated_at": 1_800_000_000,
                "local_path": "/home/PRIVATE_FOLDER",
            },
        ],
        "error_summary": [{"message": "PRIVATE_ERROR"}],
        "error_examples": [{"item_id": "PRIVATE_ITEM"}],
        "attention_examples": [{"title": "PRIVATE_TITLE"}],
        "reconcile_examples": [{"local_path": "/home/PRIVATE_FOLDER"}],
    }
    raw_before = json.dumps(report, sort_keys=True)
    exported = safe_kobo_backup_report(report)
    published = json.dumps(exported, sort_keys=True)
    assert exported["items"] == 12
    assert exported["items_blocked_by_scope"] == 4
    assert exported["statuses"] == {
        "error": 2, "complete": 3, "other": 3,
        "attention_required": 0, "reconcile_required": 4, "pending": 0,
    }
    assert exported["error_count"] == 2
    assert exported["reconcile_count"] == 4
    assert exported["sources"][0] == {
        "source_index": 1, "status": "unknown",
        "attempted": 3, "errors": 1, "failure_streak": 4,
        "total": 3, "updated_at": 12,
    }
    assert exported["sources"][1]["source_id"] == "0" * 24
    assert exported["sources"][1]["status"] == "failed"
    assert exported["sources"][1]["updated_at"] == 1_800_000_000
    for word in (
        "PRIVATE_CREDENTIAL", "PRIVATE_STATUS", "PRIVATE_SECRET", "PRIVATE_TITLE",
        "PRIVATE_TOKEN", "PRIVATE_ERROR", "PRIVATE_ITEM", "PRIVATE_FOLDER",
        "PRIVATE_REMAINING", "access_token", "local_path",
    ):
        assert word not in published
    assert json.dumps(report, sort_keys=True) == raw_before


def test_kobo_diagnostic_recovers_checkpoint_without_sessions_after_reopen(tmp_path):
    """Offline CLI cannot require a live Kobo session to show stored progress."""
    service = _service(tmp_path, manifest=[])
    key = _kobo_checkpoint_key("default", "vault-1")
    try:
        service.storage.metadata_set(key, json.dumps({
            "schema_version": 1, "status": "partial",
            "total": 522, "processed": 320, "updated_at": 1_800_000_000,
            "counts": {"attempted": 320, "errors": 314, "remaining": 202},
            "failure_streak": 2,
        }))
    finally:
        service.close()

    reopened = _service(tmp_path, manifest=[])
    try:
        reopened._sessions.clear()
        reopened.accounts.clear()
        assert reopened.storage.provider_connections()
        report = reopened.kobo_backup_diagnostic()
        assert report["sources"][0]["status"] == "partial"
        shared = safe_kobo_backup_report(report)
        assert len(shared["sources"]) == 1
        assert shared["sources"][0]["position"] == 320
        assert shared["sources"][0]["total"] == 522
        assert shared["sources"][0]["remaining"] == 202
        assert shared["sources"][0]["failure_streak"] == 2
        assert "vault-1" not in json.dumps(shared)
        assert "default" not in json.dumps(shared)
        # Checkpoint reads are passive and never reconstruct provider sessions.
        assert reopened._sessions == {}
    finally:
        reopened.close()
