"""#3275: sending Kobo A's local bytes to B must not globally block Kobo B.

Uses the real Core transfer and storage paths; no Kobo provider I/O, new
rights, acquisition, implicit adoption, or external filesystem mutation.
"""
from __future__ import annotations

from pathlib import Path

from cifereca.core.kobo_backup import _indexed_kobo_catalog_paths
from tests.test_core0925_open_destinations import _service


def _kobo_source(service, root: Path):
    src = root / "EBooks" / "alice" / "kobo" / "Book.epub"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"synthetic Kobo A book")
    placement_id = service.storage.placement_put({
        "artifact_id": "sha256:synthetic-a",
        "path": str(src), "identity_id": "alice", "provider_id": "kobo",
        "source_item_id": "book-1", "state": "present", "metadata": {},
    })
    service.storage.catalog_entry_put({
        "catalog_entry_id": "alice-kobo-book-1",
        "artifact_id": "sha256:synthetic-a", "placement_id": placement_id,
        "title": "Book", "author": "Author", "identity_id": "alice",
        "provider_id": "kobo", "item_id": "book-1", "local_state": "local",
        "media_format": "epub",
        "metadata": {"path": str(src), "source_credential": "vault-A"},
    })
    return src, placement_id


def test_received_kobo_file_is_not_global_orphan_but_stays_item_reconciliation(
    tmp_path,
):
    service = _service(tmp_path)
    try:
        src, placement_id = _kobo_source(service, tmp_path)
        result = service.materialize_to_identity(placement_id, "bob")
        target = Path(result["path"])
        assert target.is_file() and target != src
        assert service.kobo_unlinked_local_count("bob") == 0
        assert service.kobo_unlinked_local_count("alice") == 1

        # The same Kobo product in B's OWN manifest is still a per-item
        # candidate for manual reconciliation, never a proven B purchase.
        indexed = _indexed_kobo_catalog_paths(service)
        assert indexed[("bob", "book-1")] == [target]
        assert service.storage.entitlements() == []
        assert service.storage.acquisitions() == []
        assert service.storage.backup_list("kobo") == []

        # A real unrelated orphan under B must still block the general guard.
        orphan = tmp_path / "EBooks" / "bob" / "local" / "orphan.epub"
        orphan.parent.mkdir(parents=True, exist_ok=True)
        orphan.write_bytes(b"other book")
        service.storage.catalog_entry_put({
            "catalog_entry_id": "bob-orphan", "title": "Other book",
            "identity_id": "bob", "provider_id": "local",
            "item_id": "unlinked-book", "local_state": "local",
            "metadata": {"path": str(orphan)},
        })
        assert service.kobo_unlinked_local_count("bob") == 1
        assert service.materialize_to_identity(placement_id, "bob")["status"] == "unchanged"
        assert service.kobo_unlinked_local_count("bob") == 1
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
        assert service.kobo_unlinked_local_count("bob") == 0
        assert _indexed_kobo_catalog_paths(service)[("bob", "book-1")] == [target]
        assert service.storage.entitlements() == []
    finally:
        service.close()


def test_forged_received_marker_without_matching_placement_does_not_bypass_guard(
    tmp_path,
):
    service = _service(tmp_path)
    try:
        other = tmp_path / "EBooks" / "bob" / "kobo" / "not-a-transfer.epub"
        other.parent.mkdir(parents=True, exist_ok=True)
        other.write_bytes(b"unlinked local Kobo file")
        service.storage.catalog_entry_put({
            "catalog_entry_id": "bob-kobo-forged",
            "title": "Mislabelled local file", "identity_id": "bob",
            "provider_id": "kobo", "item_id": "item-2",
            "local_state": "local", "metadata": {
                "path": str(other), "transfer_kind": "local_copy",
                "materialized_from": "synthetic-false-source",
                "file_origin_identity_id": "alice",
            },
        })
        assert service.kobo_unlinked_local_count("bob") == 1
        assert _indexed_kobo_catalog_paths(service)[("bob", "item-2")] == [other]
    finally:
        service.close()
