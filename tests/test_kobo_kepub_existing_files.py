"""P0 Kobo: compound KEPUB filename variants block duplicate acquisitions.

Uses real SQLite and valid EPUB ZIPs without any downloader/provider I/O.
"""
from __future__ import annotations

import pytest

from tests.test_kobo_backup_reconcile_prune import _manifest_row, _service, _write_epub


@pytest.mark.parametrize("placement,suffix", [
    ("identity", ".kepub.epub"),
    ("identity", " [book1].kepub.epub"),
    ("legacy", ".kepub.epub"),
])
def test_existing_kepub_candidate_requires_manual_reconciliation_even_with_bypass(
    tmp_path, monkeypatch, placement, suffix,
):
    service = _service(tmp_path, manifest=[_manifest_row()])
    service.config.set("kobo_backup.allow_unlinked_acquisition", True)
    target_dir = service._kobo_backup_target_dir("default")
    directory = service.library_dir() if placement == "legacy" else target_dir
    path = directory / f"Author - Book One{suffix}"
    _write_epub(path, marker=b"existing-KEPUB-content")
    bytes_before = path.read_bytes()
    monkeypatch.setattr(
        service, "_execute_kobo_acquisition",
        lambda *_args, **_kwargs: pytest.fail(
            "pre-existing KEPUB must not trigger a duplicate download"
        ),
    )
    try:
        summary = service.sync_kobo_backup_account("vault-1")
        assert summary["status"] == "attention_required"
        assert summary["attempted"] == summary["downloaded"] == 0
        assert summary["reconcile_pending"] >= 1
        item = service.storage.backup_get("kobo", "vault-1", "book-1")
        assert item["status"] == "reconcile_required"
        assert item["local_path"] == str(path)
        assert path.read_bytes() == bytes_before

        # The quarantine is durable and cannot be bypassed on repetition.
        repeat = service.sync_kobo_backup_account("vault-1")
        assert repeat["attempted"] == repeat["downloaded"] == 0
        assert service.storage.backup_get("kobo", "vault-1", "book-1")[
            "status"
        ] == "reconcile_required"
        assert path.read_bytes() == bytes_before
    finally:
        service.close()


def test_kepub_and_epub_candidates_create_explicit_conflict_not_overwrite(
    tmp_path, monkeypatch,
):
    service = _service(tmp_path, manifest=[_manifest_row()])
    service.config.set("kobo_backup.allow_unlinked_acquisition", True)
    directory = service._kobo_backup_target_dir("default")
    kepub = directory / "Author - Book One.kepub.epub"
    epub = directory / "Author - Book One.epub"
    _write_epub(kepub, marker=b"first")
    _write_epub(epub, marker=b"second")
    before = [kepub.read_bytes(), epub.read_bytes()]
    monkeypatch.setattr(
        service, "_execute_kobo_acquisition",
        lambda *_args, **_kwargs: pytest.fail("conflicting EPUBs cannot download"),
    )
    try:
        result = service.sync_kobo_backup_account("vault-1")
        assert result["attempted"] == result["downloaded"] == 0
        assert result["reconcile_conflicts"] >= 1
        current = service.storage.backup_get("kobo", "vault-1", "book-1")
        assert current["status"] == "reconcile_conflict"
        assert current["local_path"] is None
        assert [kepub.read_bytes(), epub.read_bytes()] == before
    finally:
        service.close()
