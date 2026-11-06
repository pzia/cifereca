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


@pytest.mark.parametrize("status,attention,error", [
    ("acquiring", None, None),
    ("pending_acquisition", "interrupted_acquisition_review", None),
    ("pending_acquisition", None,
     "Acquisition locale interrompue avant finalisation ; reprise ambiguë"),
])
def test_archived_manifest_cannot_clear_interrupted_at_most_once_marker(
        tmp_path, monkeypatch, status, attention, error):
    """An archive is not proof that a previous Kobo GET was never sent."""
    from cifereca.core.kobo_backup import _skip_manifest_item
    service = _service(tmp_path, manifest=[_manifest_row()])
    try:
        service.storage.backup_put(_checkpoint(
            status=status, attention_code=attention, error=error,
            attempts=2,
        ))
        _forbid_download(service, monkeypatch)
        current = service.storage.backup_get("kobo", "vault-1", "book-1")
        item = ("rev-new", "Book One", "account", current,
                {**current, "revision_id": "rev-new"})
        summary = {"skipped_archived": 0, "reconcile_pending": 0,
                   "reconcile_skipped": 0}
        assert _skip_manifest_item(
            service, item, {**_manifest_row(), "archived": True},
            tmp_path, {}, True, {}, 0, 1, summary,
            lambda *_a, **_kw: None,
        ) is True
        assert summary["skipped_archived"] == 1
        assert service.storage.backup_get(
            "kobo", "vault-1", "book-1",
        ) == current
        # Even after unarchive, no second GET is admitted.
        assert _skip_manifest_item(
            service, item, {**_manifest_row(), "archived": False},
            tmp_path, {}, True, {}, 0, 1, summary,
            lambda *_a, **_kw: None,
        ) is True
        assert summary["reconcile_pending"] == 1
        assert service.storage.backup_get(
            "kobo", "vault-1", "book-1",
        ) == current
        assert service.storage.backup_prune_missing(
            "kobo", "vault-1", set(),
        )["pruned"] == 0
    finally:
        service.close()


def test_ordinary_pending_archived_manifest_still_records_archive(
        tmp_path, monkeypatch):
    """Non-interrupted pending state retains the existing archive behavior."""
    from cifereca.core.kobo_backup import _skip_manifest_item
    service = _service(tmp_path, manifest=[_manifest_row()])
    try:
        service.storage.backup_put(_checkpoint(
            status="pending_acquisition", attempts=0,
            error="Kobo-Adept indisponible",
        ))
        _forbid_download(service, monkeypatch)
        current = service.storage.backup_get("kobo", "vault-1", "book-1")
        summary = {"skipped_archived": 0}
        assert _skip_manifest_item(
            service, ("rev-new", "Book One", "account", current,
                      {**current, "revision_id": "rev-new"}),
            {**_manifest_row(), "archived": True},
            tmp_path, {}, True, {}, 0, 1, summary,
            lambda *_a, **_kw: None,
        ) is True
        assert summary["skipped_archived"] == 1
        assert service.storage.backup_get(
            "kobo", "vault-1", "book-1",
        )["status"] == "skipped_archived"
    finally:
        service.close()


def test_real_legacy_sync_archive_then_unarchive_preserves_crash_checkpoint(
        tmp_path, monkeypatch):
    """Exercise recovery + manifest refresh + archived skip, not only helper."""
    raw = {**_manifest_row(revision_id="rev-new"), "archived": True}
    service = _service(tmp_path, manifest=[raw])
    try:
        service.storage.backup_put(_checkpoint(
            status="acquiring", revision="rev-before-crash",
        ))
        _forbid_download(service, monkeypatch)
        archived = service.sync_kobo_backup()
        stored = service.storage.backup_get("kobo", "vault-1", "book-1")
        assert archived["attempted"] == archived["downloaded"] == 0
        assert archived["skipped_archived"] == 1
        assert stored["status"] == "pending_acquisition"
        assert stored["attention_code"] == "interrupted_acquisition_review"
        assert stored["revision_id"] == "rev-before-crash"
        # Manifest changes without a new user-triggered borrowing/download.
        raw["archived"] = False
        unarchived = service.sync_kobo_backup()
        assert unarchived["attempted"] == unarchived["downloaded"] == 0
        assert unarchived["reconcile_pending"] == 1
        after = service.storage.backup_get("kobo", "vault-1", "book-1")
        # _refresh_manifest_record may update the observation timestamp on
        # the next second. Compare durable crash/retry evidence, not the
        # wall-clock value of an otherwise no-op metadata refresh.
        assert after["updated_at"] >= stored["updated_at"]
        assert {k: v for k, v in after.items() if k != "updated_at"} == {
            k: v for k, v in stored.items() if k != "updated_at"
        }
    finally:
        service.close()


@pytest.mark.parametrize("status,error", [
    ("acquiring", None),
    ("error", "Kobo-Adept : réponse JSON invalide"),
])
@pytest.mark.parametrize("sha,size", [
    (None, None), (None, 43), ("a" * 64, 43),
])
def test_single_interrupted_candidate_never_mints_byte_baseline(
    tmp_path, monkeypatch, status, error, sha, size,
):
    """The locator is only a recovery lead; no bytes or Kobo rights are proven."""
    from cifereca.core import kobo_backup
    from tests.test_kobo_backup_reconcile_prune import _write_epub

    service = _service(tmp_path, manifest=[_manifest_row()])
    try:
        path = service._kobo_backup_target_dir("default") / "Author - Book One.epub"
        _write_epub(path, marker=b"candidate-not-a-provider-receipt")
        original = path.read_bytes()
        service.storage.backup_put({
            **_checkpoint(status=status, revision="rev-1", error=error),
            "sha256": sha, "size": size,
        })
        before_links = service.storage.origin_links()
        _forbid_download(service, monkeypatch)
        # A recovered candidate must not be hashed to complete its previously
        # missing legacy SHA, including via the next reconciliation pass.
        monkeypatch.setattr(
            kobo_backup, "sha256_file",
            lambda *_a, **_k: pytest.fail("candidate must not become hash proof"),
        )
        first = service.sync_kobo_backup_account("vault-1")
        assert first["attempted"] == first["downloaded"] == first["unchanged"] == 0
        assert first["reconcile_skipped"] >= 1
        after = service.storage.backup_get("kobo", "vault-1", "book-1")
        assert after["status"] == "reconcile_required"
        assert after["revision_id"] == "rev-1"
        assert after["sha256"] == sha and after["size"] == size
        assert after["local_path"] == str(path)
        assert path.read_bytes() == original
        assert service.storage.origin_links() == before_links
        second = service.sync_kobo_backup_account("vault-1")
        again = service.storage.backup_get("kobo", "vault-1", "book-1")
        assert second["attempted"] == second["downloaded"] == second["unchanged"] == 0
        assert again["sha256"] == sha and again["size"] == size
        assert again["local_path"] == str(path)
        assert path.read_bytes() == original
        assert service.storage.origin_links() == before_links
    finally:
        service.close()
