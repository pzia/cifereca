"""#3196: a Kobo recovery collision must not erase the last 0.7 local locator.

Synthetic files/manifest only. No Kobo HTTP, ADEPT, user data, or download.
"""
from __future__ import annotations

import hashlib

import pytest

from tests.test_kobo_backup_reconcile_prune import (
    _manifest_row, _service, _write_epub,
)


@pytest.mark.parametrize("old_pointer", ["existing", "offline"])
@pytest.mark.parametrize("in_manifest", [False, True])
def test_conflicted_recovery_keeps_original_locator_and_never_redownloads(
    tmp_path, monkeypatch, old_pointer, in_manifest,
):
    service = _service(
        tmp_path, manifest=[_manifest_row()] if in_manifest else [],
    )
    try:
        directory = service._kobo_backup_target_dir("default")
        first = directory / "Author - Book One.epub"
        second = directory / "Author - Book One [book1].epub"
        _write_epub(first, b"candidate-a")
        _write_epub(second, b"candidate-b")
        historical = (
            first if old_pointer == "existing"
            else directory / "Historic offline purchase.epub"
        )
        baseline = hashlib.sha256(b"historical-unverified").hexdigest()
        service.storage.backup_put({
            "provider_id": "kobo",
            "credential_id": "vault-1",
            "identity_id": "default",
            "account_id": "account",
            "item_id": "book-1",
            "revision_id": "rev-1",
            "title": "Book One",
            "author": "Author",
            "format": "EPUB",
            "status": "acquiring",
            "local_path": str(historical),
            "sha256": baseline,
        })
        original_files = (first.read_bytes(), second.read_bytes())
        monkeypatch.setattr(
            service, "_execute_kobo_acquisition",
            lambda *_args, **_kwargs: pytest.fail(
                "ambiguous local candidates must never trigger a GET"
            ),
        )

        first_sync = service.sync_kobo_backup()
        receipt = service.storage.backup_get("kobo", "vault-1", "book-1")
        assert first_sync["recovered_interrupted"] == 1
        assert first_sync["downloaded"] == 0
        assert first_sync["legacy_pruned"] == 0
        assert receipt is not None
        assert receipt["status"] == "reconcile_conflict"
        assert receipt["local_path"] == str(historical)
        assert receipt["sha256"] == baseline
        assert (first.read_bytes(), second.read_bytes()) == original_files

        second_sync = service.sync_kobo_backup()
        repeated = service.storage.backup_get("kobo", "vault-1", "book-1")
        assert second_sync["downloaded"] == 0
        assert second_sync["legacy_pruned"] == 0
        assert repeated["status"] == "reconcile_conflict"
        assert repeated["local_path"] == str(historical)
        assert repeated["sha256"] == baseline
        assert (first.read_bytes(), second.read_bytes()) == original_files
    finally:
        service.close()
