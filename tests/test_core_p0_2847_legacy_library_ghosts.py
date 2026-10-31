"""P0 #2847/#3196: legacy backup catalogue placeholders are not local books.

No provider calls, network or destructive migration. SQLite history remains.
"""
from __future__ import annotations

import hashlib

import pytest

from tests.test_kobo_backup_guard import _service, _write_epub


def _legacy(path):
    raw = b"synthetic original"
    return {
        "provider_id": "kobo", "credential_id": "vault-1",
        "account_id": "account", "identity_id": "alice",
        "item_id": "book-1", "revision_id": "rev-1",
        "title": "Legacy invented local", "entitlement_type": "owned",
        "status": "complete", "local_path": str(path),
        "sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw),
        "format": "epub",
    }


@pytest.mark.parametrize("physical_exists", [False, True])
def test_unverified_backup_placeholder_never_announces_local_presence(
    tmp_path, physical_exists,
):
    service = _service(tmp_path)
    try:
        path = tmp_path / "EBooks" / "alice" / "kobo" / "legacy.epub"
        if physical_exists:
            _write_epub(path)
        service.storage.backup_put(_legacy(path))
        recorded = service.storage.catalog_entries()
        assert len(recorded) == 1
        assert recorded[0]["local_state"] == "local"
        assert recorded[0]["metadata"]["imported_from"] == "backup_items"
        assert service.storage.placements()[0]["state"] == "unverified"
        books = service.library_entries(identity_id="alice", scan=False)
        assert not any(
            book.get("local") is True and book.get("item_id") == "book-1"
            for book in books
        )
        # Keep original history, original path and bytes for later repair.
        assert service.storage.backup_get("kobo", "vault-1", "book-1")["local_path"] == str(path)
        assert service.storage.catalog_entries() == recorded
        assert path.exists() is physical_exists
    finally:
        service.close()


def test_unrelated_present_placement_same_artifact_cannot_promote_legacy_catalogue(
    tmp_path,
):
    service = _service(tmp_path)
    try:
        missing = tmp_path / "EBooks" / "alice" / "kobo" / "lost.epub"
        legacy = _legacy(missing)
        service.storage.backup_put(legacy)
        actual = tmp_path / "EBooks" / "alice" / "local" / "other.epub"
        _write_epub(actual)
        # Same declared Artifact SHA but another placement ID and different
        # file: its presence cannot validate the exact legacy source path.
        service.storage.placement_put({
            "path": str(actual), "identity_id": "alice",
            "state": "present", "artifact_id": "sha256:" + legacy["sha256"],
            "provider_id": "local", "source_item_id": "different",
        })
        books = service.library_entries(identity_id="alice", scan=False)
        assert not any(
            book.get("local") is True and book.get("item_id") == "book-1"
            for book in books
        )
        assert service.storage.backup_get("kobo", "vault-1", "book-1")["status"] == "complete"
    finally:
        service.close()


def test_promoted_legacy_exact_present_placement_can_be_projected(tmp_path):
    service = _service(tmp_path)
    try:
        path = tmp_path / "EBooks" / "alice" / "kobo" / "known.epub"
        _write_epub(path)
        legacy = _legacy(path)
        service.storage.backup_put(legacy)
        placement = service.storage.placements()[0]
        service.storage.placement_put({
            "placement_id": placement["placement_id"],
            "path": str(path), "identity_id": "alice",
            "state": "present",
            "artifact_id": "sha256:" + legacy["sha256"],
            "provider_id": "kobo", "source_item_id": "book-1",
        })
        books = service.library_entries(identity_id="alice", scan=False)
        assert any(
            book.get("local") is True and book.get("item_id") == "book-1"
            for book in books
        )
    finally:
        service.close()
