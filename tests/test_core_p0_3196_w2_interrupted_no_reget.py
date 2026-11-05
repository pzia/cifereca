"""#3196/W2: a crash checkpoint is not consent to a second legacy Kobo GET.

Synthetic v0.7 storage and Vault manifest; fail if any provider/ADEPT
content executor is called for the exact interrupted edition.
"""
from __future__ import annotations

import pytest

from tests.test_kobo_backup_reconcile_prune import _manifest_row, _service


def _checkpoint(*, status: str, revision="rev-before-crash",
                error=None, attention_code=None, attempts=1):
    return {
        "provider_id": "kobo", "credential_id": "vault-1",
        "identity_id": "default", "account_id": "account",
        "item_id": "book-1", "revision_id": revision,
        "title": "Book One", "author": "Author", "format": "EPUB",
        "entitlement_type": "owned", "status": status,
        "local_path": None, "size": None, "sha256": None,
        "attempt_count": attempts, "job_id": "interrupted-job",
        "last_attempt_at": 123, "attention_code": attention_code,
        "error": error,
    }


def _forbid_download(service, monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("interrupted legacy Kobo must not start a second GET")
    monkeypatch.setattr(service, "_execute_kobo_acquisition", forbidden)


def test_acquiring_with_still_listed_manifest_is_recovered_but_not_retried(
    tmp_path, monkeypatch,
):
    service = _service(tmp_path, manifest=[_manifest_row(revision_id="rev-after-crash")])
    try:
        service.storage.backup_put(_checkpoint(status="acquiring"))
        _forbid_download(service, monkeypatch)
        first = service.sync_kobo_backup()
        observed = service.storage.backup_get("kobo", "vault-1", "book-1")
        assert first["recovered_interrupted"] == 1
        assert first["attempted"] == first["downloaded"] == 0
        assert first["reconcile_skipped"] == first["reconcile_pending"] == 1
        assert observed is not None
        assert observed["status"] == "pending_acquisition"
        assert observed["attention_code"] == "interrupted_acquisition_review"
        assert observed["revision_id"] == "rev-before-crash"
        assert observed["attempt_count"] == 1
        assert observed["local_path"] is None

        second = service.sync_kobo_backup()
        assert second["recovered_interrupted"] == 0
        assert second["attempted"] == second["downloaded"] == 0
        assert second["reconcile_skipped"] == second["reconcile_pending"] == 1
        assert service.storage.backup_get("kobo", "vault-1", "book-1") == observed
        assert service.storage.placements() == []
    finally:
        service.close()


def test_historical_pending_interruption_without_new_marker_is_still_quarantined(
    tmp_path, monkeypatch,
):
    service = _service(tmp_path, manifest=[_manifest_row(revision_id="rev-new")])
    try:
        previous = _checkpoint(
            status="pending_acquisition", attempts=2,
            error="Acquisition locale interrompue avant finalisation ; nouvelle tentative nécessaire",
        )
        service.storage.backup_put(previous)
        _forbid_download(service, monkeypatch)
        before = service.storage.backup_get("kobo", "vault-1", "book-1")
        first = service.sync_kobo_backup()
        assert first["attempted"] == first["downloaded"] == 0
        assert first["reconcile_skipped"] == first["reconcile_pending"] == 1
        after = service.storage.backup_get("kobo", "vault-1", "book-1")
        assert after["revision_id"] == "rev-before-crash"
        assert after["error"] == before["error"]
        assert after["attempt_count"] == before["attempt_count"]
        assert after["attention_code"] is None
        assert service.storage.placements() == []
    finally:
        service.close()


def test_pending_without_interrupted_attempt_proof_is_not_reclassified(
    tmp_path, monkeypatch,
):
    service = _service(tmp_path, manifest=[_manifest_row()])
    calls = []
    try:
        # An unavailable adapter before any attempt may have written this
        # ordinary pending state. It remains eligible for the legacy path;
        # this new fence must not create an implicit permanent quarantine.
        service.storage.backup_put(_checkpoint(
            status="pending_acquisition", revision="rev-1",
            attempts=0, error="Kobo-Adept indisponible",
        ))
        def synthetic_executor(*_args, **_kwargs):
            calls.append("attempt")
            raise RuntimeError("synthetic local adapter failure")
        monkeypatch.setattr(
            service, "_execute_kobo_acquisition", synthetic_executor,
        )
        summary = service.sync_kobo_backup()
        assert summary["reconcile_skipped"] == 0
        assert summary["attempted"] == 1
        # This synthetic legacy session has no normalize_item implementation;
        # that old provider ABI fails *after* attempt admission but before
        # invoking _execute_kobo_acquisition. The distinction proved here is
        # that ordinary adapter-pending rows are not quarantined by W2.
        assert summary["errors"] == 1
        assert calls == []
    finally:
        service.close()


def test_raw_acquiring_checkpoint_without_remnant_scan_never_restarts_get(
    tmp_path, monkeypatch,
):
    service = _service(tmp_path, manifest=[_manifest_row()])
    try:
        service.storage.backup_put(_checkpoint(status="acquiring"))
        _forbid_download(service, monkeypatch)
        # The private admission shortcut is exercised directly with the
        # original checkpoint, as the confirmed-manifest path skips recovery.
        from cifereca.core.kobo_backup import _skip_manifest_item
        current = service.storage.backup_get("kobo", "vault-1", "book-1")
        row = _manifest_row()
        base = {**current, "revision_id": "rev-1"}
        summary = {"reconcile_pending": 0, "reconcile_skipped": 0}
        assert _skip_manifest_item(
            service, ("rev-1", "Book One", "account", current, base),
            row, tmp_path, {}, True, {}, 1, 1,
            summary, lambda *_a, **_kw: None,
        ) is True
        assert summary == {"reconcile_pending": 1, "reconcile_skipped": 1}
    finally:
        service.close()
