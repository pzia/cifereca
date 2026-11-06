"""#3196 W3: a missing Kobo manifest item cannot close manual reconciliation.

Synthetic SQLite and disposable EPUB candidates only, no provider HTTP.
"""
from __future__ import annotations

import pytest

from cifereca.storage import Storage
from tests.test_kobo_backup_reconcile_prune import (
    _service, _write_epub,
)


@pytest.mark.parametrize("state", ["reconcile_required", "reconcile_conflict"])
def test_unresolved_no_path_kobo_quarantine_survives_manifest_prune(
    tmp_path, state,
):
    storage = Storage(tmp_path / "legacy.sqlite3")
    try:
        def row(item, *, status=state, provider="kobo"):
            return {
                "provider_id": provider, "credential_id": "vault",
                "identity_id": "alice", "account_id": "buyer",
                "item_id": item, "title": "Synthetic legacy",
                "entitlement_type": "owned",
                "status": status, "local_path": None,
            }

        storage.backup_put(row("manual"))
        storage.backup_put(row("stale", status="complete"))
        storage.backup_put(row("other-provider", provider="unrelated-provider"))
        before = storage.backup_get("kobo", "vault", "manual")
        rights = storage.entitlements()
        receipts = storage.acquisitions()
        result = storage.backup_prune_missing("kobo", "vault", set())
        assert result == {
            "pruned": 0, "local_preserved": 0,
            "entitlements_inactivated": 0,
        }
        assert storage.backup_get("kobo", "vault", "manual") == before
        assert storage.backup_get("kobo", "vault", "stale") is not None
        assert storage.backup_get("unrelated-provider", "vault", "other-provider")
        assert storage.entitlements() == rights
        assert storage.acquisitions() == receipts
        # The Kobo-specific admission must not alter other providers.
        other = storage.backup_prune_missing(
            "unrelated-provider", "vault", set(),
        )
        assert other["pruned"] == 1
        assert storage.backup_get(
            "unrelated-provider", "vault", "other-provider",
        ) is None
    finally:
        storage.close()


def test_multi_candidate_recovery_without_previous_path_survives_empty_manifest(
    tmp_path, monkeypatch,
):
    service = _service(tmp_path, manifest=[])
    try:
        directory = service._kobo_backup_target_dir("default")
        first = directory / "Author - Book One.epub"
        second = directory / "Author - Book One [book1].epub"
        _write_epub(first, b"candidate-A")
        _write_epub(second, b"candidate-B")
        original_bytes = (first.read_bytes(), second.read_bytes())
        service.storage.backup_put({
            "provider_id": "kobo", "credential_id": "vault-1",
            "identity_id": "default", "account_id": "account",
            "item_id": "book-1", "revision_id": "rev-1",
            "title": "Book One", "author": "Author",
            "format": "EPUB", "status": "error",
            "error": "Kobo-Adept : réponse JSON invalide",
            "local_path": None,
        })
        monkeypatch.setattr(
            service, "_execute_kobo_acquisition",
            lambda *_args, **_kwargs: pytest.fail(
                "no automatic download in an unresolved Kobo collision"
            ),
        )

        for _ in range(2):
            summary = service.sync_kobo_backup()
            row = service.storage.backup_get("kobo", "vault-1", "book-1")
            assert summary["downloaded"] == 0
            assert summary["legacy_pruned"] == 0
            assert summary["reconcile_conflicts"] >= 1
            assert row is not None
            assert row["status"] == "reconcile_conflict"
            assert row["local_path"] is None
            assert (first.read_bytes(), second.read_bytes()) == original_bytes
    finally:
        service.close()


def test_missing_local_reconciliation_is_not_erased_by_empty_manifest(
    tmp_path, monkeypatch,
):
    service = _service(tmp_path, manifest=[])
    try:
        service.storage.backup_put({
            "provider_id": "kobo", "credential_id": "vault-1",
            "identity_id": "default", "account_id": "account",
            "item_id": "historical-edition", "revision_id": "old-revision",
            "title": "Historical missing download", "format": "EPUB",
            "status": "reconcile_required", "local_path": None,
            "error": "historical receipt: unresolved local copy",
        })
        monkeypatch.setattr(
            service, "_execute_kobo_acquisition",
            lambda *_args, **_kwargs: pytest.fail(
                "manifest absence must not authorize a repeat download"
            ),
        )
        before = service.storage.backup_get("kobo", "vault-1", "historical-edition")
        for _ in range(2):
            summary = service.sync_kobo_backup()
            assert summary["downloaded"] == 0
            assert summary["legacy_pruned"] == 0
            assert summary["reconcile_pending"] >= 1
            assert service.storage.backup_get(
                "kobo", "vault-1", "historical-edition",
            ) == before
    finally:
        service.close()
