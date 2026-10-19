"""P0 #1143: Kobo per-item failure logs and durable errors exclude secrets.

The real Core+SQLite Kobo backup flow is exercised with synthetic manifest
items and stubs. No provider network, account login, Adobe or ZIP download.
"""
from __future__ import annotations

import json
import logging

import pytest

from cifereca.kobo.errors import KoboAdeptError
from cifereca.kobo.vault import KoboVaultError
from tests.test_kobo_backup_observability import _service


SECRET = "SENSITIVE_KOBO_TOKEN_ABC"
SIGNED = "https://private.invalid/LOAN/reader.acsm?auth=" + SECRET
ITEM = "PRIVATE_KOBO_PRODUCT_ID"
TITLE = "PRIVATE_KOBO_BOOK_TITLE"


def _setup(tmp_path):
    return _service(tmp_path, manifest=[{
        "product_id": ITEM, "revision_id": "rev-1", "title": TITLE,
        "format": "EPUB", "content_type": "ebook",
        "owned": True, "downloadable": True,
    }])


def test_kobo_vault_failure_is_typed_in_logs_and_constant_in_sqlite(
    tmp_path, monkeypatch, caplog,
):
    service, session = _setup(tmp_path)
    def deny(*_args, **_kwargs):
        raise KoboVaultError(
            SIGNED, code=SECRET, status_code=403,
        )
    monkeypatch.setattr(session, "resolve_acquisition", deny)
    try:
        with caplog.at_level(logging.WARNING, logger="cifereca"):
            result = service.sync_kobo_backup()
        assert result["attempted"] == result["errors"] == 1
        saved = service.storage.backup_get("kobo", "vault-1", ITEM)
        assert saved["status"] == "error"
        assert saved["error"] == "Sauvegarde Kobo impossible"
        # The in-process summary is now typed and redacted too.
        diagnostic = service.diagnostics.snapshot()[-1]["detail"]
        assert diagnostic["error_summary"][0]["code"] == "provider_http_error"
        assert SIGNED not in json.dumps(diagnostic)
        failures = [
            line for line in caplog.text.splitlines()
            if "TRACE kobo acquisition failed" in line
        ]
        assert len(failures) == 1
        assert "code=backup_item_failed" in failures[0]
        assert "phase=vault" in failures[0]
        assert "http_status=403" in failures[0]
        for private in (SECRET, SIGNED, ITEM, TITLE, "vault-1", "default"):
            assert private not in failures[0]
        assert SECRET not in saved["error"]
    finally:
        service.close()


def test_kobo_adept_unparseable_artifact_keeps_reconciliation_without_exception(
    tmp_path, monkeypatch,
):
    service, _session = _setup(tmp_path)
    def fail_adept(_request):
        raise KoboAdeptError(
            "ADEPT " + SIGNED, code="result_unparseable_artifact_present",
            details={
                "artifact_path": str(tmp_path / "reconciliation.epub"),
                "artifact_size": 123,
                "artifact_sha256": "a" * 64,
            },
        )
    monkeypatch.setattr(service, "execute_acquisition_request", fail_adept)
    try:
        summary = service.sync_kobo_backup()
        saved = service.storage.backup_get("kobo", "vault-1", ITEM)
        assert saved["status"] == "reconcile_required"
        assert saved["size"] == 123
        assert saved["error"] == "Rapprochement Kobo nécessaire"
        assert summary["reconcile_pending"] == 1
        assert "SENSITIVE_KOBO_TOKEN_ABC" not in saved["error"]
        # Never confuse reconciliation with a successfully downloaded file.
        assert summary["downloaded"] == 0
    finally:
        service.close()
