"""Kobo P0: a valid EPUB whose bytes changed must not be treated as unchanged.

SQLite and EPUB files are real; acquisition hooks are blocked, no network.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from tests.test_kobo_backup_reconcile_prune import _manifest_row, _service, _write_epub


def _complete_local_backup(service, path: Path, *, sha256=None, size=None) -> None:
    service.storage.backup_put({
        "provider_id": "kobo", "credential_id": "vault-1",
        "identity_id": "default", "item_id": "book-1", "revision_id": "rev-1",
        "title": "Book One", "author": "Author", "format": "EPUB",
        "status": "complete", "local_path": str(path),
        "sha256": sha256, "size": size,
    })


def _no_download(monkeypatch, service) -> None:
    monkeypatch.setattr(
        service, "_execute_kobo_acquisition",
        lambda *_args, **_kwargs: pytest.fail("modified local EPUB must never be redownloaded"),
    )


def test_modified_but_valid_epub_preserves_original_hash_across_restarts(tmp_path, monkeypatch):
    service = _service(tmp_path, manifest=[_manifest_row()])
    path = service._kobo_backup_target_dir("default") / "book.epub"
    _write_epub(path, marker=b"alpha-version")
    original = path.read_bytes()
    original_size = path.stat().st_size
    original_sha = hashlib.sha256(original).hexdigest()
    _complete_local_backup(service, path, sha256=original_sha, size=original_size)
    # A different *valid* EPUB with the same length passes format checks.
    _write_epub(path, marker=b"omega-version")
    modified = path.read_bytes()
    assert modified != original and path.stat().st_size == original_size
    _no_download(monkeypatch, service)
    try:
        result = service.sync_kobo_backup_account("vault-1")
        assert result["status"] == "attention_required"
        assert result["attempted"] == result["downloaded"] == result["unchanged"] == 0
        assert result["reconcile_pending"] >= 1
        current = service.storage.backup_get("kobo", "vault-1", "book-1")
        assert current["status"] == "reconcile_required"
        assert current["sha256"] == original_sha
        assert current["size"] == original_size
        assert path.read_bytes() == modified
        repeated = service.sync_kobo_backup_account("vault-1")
        assert repeated["attempted"] == repeated["downloaded"] == 0
        assert service.storage.backup_get("kobo", "vault-1", "book-1")["sha256"] == original_sha
    finally:
        service.close()

    # A new process must not launder the checksum during reconciliation.
    service = _service(tmp_path, manifest=[_manifest_row()])
    _no_download(monkeypatch, service)
    try:
        repeated = service.sync_kobo_backup_account("vault-1")
        assert repeated["attempted"] == repeated["downloaded"] == 0
        row = service.storage.backup_get("kobo", "vault-1", "book-1")
        assert row["status"] == "reconcile_required"
        assert row["sha256"] == original_sha and row["size"] == original_size
        assert path.read_bytes() == modified
    finally:
        service.close()


def test_size_change_quarantined_even_with_no_historical_checksum(tmp_path, monkeypatch):
    service = _service(tmp_path, manifest=[_manifest_row()])
    path = service._kobo_backup_target_dir("default") / "book.epub"
    _write_epub(path, marker=b"short")
    previous_size = path.stat().st_size
    _complete_local_backup(service, path, sha256=None, size=previous_size)
    _write_epub(path, marker=b"longer-content-with-change")
    assert path.stat().st_size != previous_size
    _no_download(monkeypatch, service)
    try:
        summary = service.sync_kobo_backup_account("vault-1")
        assert summary["attempted"] == summary["downloaded"] == 0
        current = service.storage.backup_get("kobo", "vault-1", "book-1")
        assert current["status"] == "reconcile_required"
        assert current["size"] == previous_size
        assert not current["sha256"]
    finally:
        service.close()


@pytest.mark.parametrize("sha,size", [
    (None, None), (None, 125), ("invalid", 125),
    ("z" * 64, 125), ("f" * 64, None), ("f" * 64, 0),
])
def test_historical_complete_without_integrity_baseline_requires_explicit_reconciliation(
    tmp_path, monkeypatch, sha, size,
):
    # Neither a matching filename nor valid EPUB structure may invent the
    # SHA baseline for the old Kobo edition. Preserve the file and legacy
    # claim; do not open/hash the bytes and do not perform a second GET.
    service = _service(tmp_path, manifest=[_manifest_row()])
    path = service._kobo_backup_target_dir("default") / "legacy.epub"
    _write_epub(path, marker=b"historic-valid-file")
    original = path.read_bytes()
    _complete_local_backup(service, path, sha256=sha, size=size)
    _no_download(monkeypatch, service)
    monkeypatch.setattr(
        service, "_observed_backup_artifact",
        lambda *_a, **_k: pytest.fail("unattested filename must never be hashed"),
    )
    try:
        summary = service.sync_kobo_backup_account("vault-1")
        assert summary["unchanged"] == summary["attempted"] == summary["downloaded"] == 0
        assert summary["reconcile_skipped"] >= 1
        stored = service.storage.backup_get("kobo", "vault-1", "book-1")
        assert stored["status"] == "reconcile_required"
        assert stored["sha256"] == sha and stored["size"] == size
        assert stored["local_path"] == str(path)
        assert path.read_bytes() == original
        repeated = service.sync_kobo_backup_account("vault-1")
        assert repeated["unchanged"] == repeated["attempted"] == repeated["downloaded"] == 0
        assert service.storage.backup_get("kobo", "vault-1", "book-1")["sha256"] == sha
        assert path.read_bytes() == original
    finally:
        service.close()
