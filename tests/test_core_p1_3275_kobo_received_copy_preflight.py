"""#3275: transferring a Kobo-origin local artifact does not grant recipient B rights.

Uses the real Core transfer and storage paths; no Kobo provider I/O, new
rights, acquisition, implicit adoption, or external filesystem mutation.
"""
from __future__ import annotations

from pathlib import Path
from cifereca.library_files import sha256_file
from tests.test_core0925_open_destinations import _service
from tests.test_kobo_backup_guard import _write_epub


def _kobo_source(service, root: Path):
    src = root / "EBooks" / "alice" / "kobo" / "Book.epub"
    src.parent.mkdir(parents=True, exist_ok=True)
    _write_epub(src, b"synthetic Kobo A book")
    artifact_id = "sha256:" + sha256_file(src)
    placement_id = service.storage.placement_put({
        "artifact_id": artifact_id,
        "path": str(src), "identity_id": "alice", "provider_id": "kobo",
        "source_item_id": "book-1", "state": "present", "metadata": {},
    })
    service.storage.catalog_entry_put({
        "catalog_entry_id": "alice-kobo-book-1",
        "artifact_id": artifact_id, "placement_id": placement_id,
        "title": "Book", "author": "Author", "identity_id": "alice",
        "provider_id": "kobo", "item_id": "book-1", "local_state": "local",
        "media_format": "epub",
        "metadata": {"path": str(src), "source_credential": "vault-A"},
    })
    return src, placement_id


def test_received_kobo_file_stays_local_transfer_without_provider_rights(
    tmp_path,
):
    service = _service(tmp_path)
    try:
        src, placement_id = _kobo_source(service, tmp_path)
        result = service.materialize_to_identity(placement_id, "bob")
        target = Path(result["path"])
        assert target.is_file() and target != src

        # A local catalogue copy remains a file, not proof of Kobo rights.
        assert any(
            row.get("identity_id") == "bob"
            and row.get("provider_id") == "kobo"
            and row.get("item_id") == "book-1"
            and (row.get("metadata") or {}).get("path") == str(target)
            for row in service.storage.catalog_entries()
        )
        assert service.storage.entitlements() == []
        assert service.storage.acquisitions() == []
        assert service.storage.backup_list("kobo") == []

        # Unrelated local bytes are preserved; the copy is not a grant.
        orphan = tmp_path / "EBooks" / "bob" / "local" / "orphan.epub"
        orphan.parent.mkdir(parents=True, exist_ok=True)
        orphan.write_bytes(b"other book")
        service.storage.catalog_entry_put({
            "catalog_entry_id": "bob-orphan", "title": "Other book",
            "identity_id": "bob", "provider_id": "local",
            "item_id": "unlinked-book", "local_state": "local",
            "metadata": {"path": str(orphan)},
        })
        assert service.materialize_to_identity(placement_id, "bob")["status"] == "unchanged"
        assert service.storage.entitlements() == []
    finally:
        service.close()


def test_received_kobo_copy_remains_distinct_after_catalogue_scan(tmp_path):
    service = _service(tmp_path)
    try:
        src, placement_id = _kobo_source(service, tmp_path)
        target = Path(service.materialize_to_identity(placement_id, "bob")["path"])
        service.catalog_scan()
        assert target.is_file() and src.is_file()
        assert service.storage.entitlements() == []
    finally:
        service.close()
