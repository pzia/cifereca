"""#3275: sending Kobo A's local bytes to B must not globally block Kobo B.

Uses the real Core transfer and storage paths; no Kobo provider I/O, new
rights, acquisition, implicit adoption, or external filesystem mutation.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from cifereca.core.kobo_backup import _indexed_kobo_catalog_paths
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


def test_b_owns_same_kobo_product_but_received_a_copy_requires_explicit_link(
    tmp_path, monkeypatch,
):
    """B's own Vault manifest does not adopt a locally received copy as backup.

    The same product ID appears in A's transferred file and B's independent,
    account-scoped Kobo manifest. A repeated B sync must not download,
    mark an acquisition complete or duplicate placements while awaiting linkage.
    """
    from tests.test_kobo_backup_guard import (
        KoboSession,
        _manifest_row,
        _service as kobo_backup_service,
    )

    service = kobo_backup_service(tmp_path)
    try:
        src, source_placement_id = _kobo_source(service, tmp_path)
        sent = service.materialize_to_identity(source_placement_id, "bob")
        received = Path(sent["path"])
        original_bytes = src.read_bytes()
        assert received.read_bytes() == original_bytes
        assert service.kobo_unlinked_local_count("bob") == 0

        service.storage.provider_connection_put({
            "identity_id": "bob", "provider_id": "kobo",
            "credential_id": "vault-b", "label": "Bob Kobo",
        })
        a_session = service._sessions[service._session_key("kobo", "vault-1")]
        b_session = KoboSession(
            descriptor=a_session.descriptor,
            credential_id="vault-b",
            label="Bob Kobo",
            credentials={},
            options={},
            manifest=[_manifest_row()],
        )
        b_session.connect()
        service._sessions[service._session_key("kobo", "vault-b")] = b_session
        monkeypatch.setattr(
            service, "_execute_kobo_acquisition",
            lambda *_args, **_kwargs: pytest.fail(
                "Received A bytes must not become B's automatic download"
            ),
        )

        first = service.sync_kobo_backup_account("vault-b")
        linked = service.storage.backup_get("kobo", "vault-b", "book-1")
        assert linked is not None
        assert linked["identity_id"] == "bob"
        assert linked["credential_id"] == "vault-b"
        assert linked["status"] in {"reconcile_required", "reconcile_conflict"}
        assert first["attempted"] == 0 and first["downloaded"] == 0
        assert first["reconcile_pending"] == 1
        assert service.storage.backup_get("kobo", "vault-1", "book-1") is None
        # B's positive OWN manifest creates a legitimate discovery in the
        # acquisition ledger. It is NOT a completed download of A's bytes.
        discoveries = service.storage.acquisitions()
        assert len(discoveries) == 1
        assert discoveries[0]["credential_id"] == "vault-b"
        assert discoveries[0]["identity_id"] == "bob"
        assert discoveries[0]["item_id"] == "book-1"
        assert discoveries[0]["status"] == "discovered"
        assert discoveries[0]["attempt_count"] == 0
        assert discoveries[0]["artifact_id"] is None

        second = service.sync_kobo_backup_account("vault-b")
        assert second["attempted"] == 0 and second["downloaded"] == 0
        assert second["reconcile_skipped"] >= 1
        assert service.storage.backup_get("kobo", "vault-b", "book-1")["status"] == linked["status"]
        assert service.materialize_to_identity(source_placement_id, "bob")["status"] == "unchanged"
        assert len([p for p in service.storage.placements()
                    if p.get("identity_id") == "bob"
                    and p.get("metadata", {}).get("materialized_from") == source_placement_id]) == 1
        assert src.read_bytes() == original_bytes
        assert received.read_bytes() == original_bytes
        again_discoveries = service.storage.acquisitions()
        assert len(again_discoveries) == 1
        assert again_discoveries[0]["acquisition_id"] == discoveries[0]["acquisition_id"]
        assert again_discoveries[0]["status"] == "discovered"
        assert again_discoveries[0]["attempt_count"] == 0
        assert again_discoveries[0]["artifact_id"] is None
    finally:
        service.close()
