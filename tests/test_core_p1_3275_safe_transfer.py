"""#3275: a file sent between identities is an independent local copy.

All fixtures stay in tmp_path; no supplier I/O, entitlement or acquisition.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from cifereca.plugin_api import write_bundle_manifest
from cifereca.providers import ProviderConfigurationError
from tests.test_core0925_open_destinations import _placement, _service


def _source(service, root: Path, *, filename="Book.epub", data=b"book-content"):
    path = root / "EBooks" / "alice" / "local" / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    placement = _placement(service, path)
    service.storage.catalog_entry_put({
        "catalog_entry_id": "alice-copy-source", "artifact_id": "sha256:test",
        "placement_id": placement, "title": "Book", "author": "Author",
        "identity_id": "alice", "provider_id": "local", "item_id": "book",
        "local_state": "local", "media_format": "epub",
        "metadata": {"path": str(path), "source_credential": "private-card-A",
                     "rights": "signed-access-A", "owned": True,
                     "description": "Public description"},
    })
    return path, placement


def test_file_transfer_is_independent_idempotent_and_never_transfers_rights(tmp_path):
    service = _service(tmp_path)
    src, placement = _source(service, tmp_path)
    before_entitlements = service.storage.entitlements()
    before_acquisitions = service.storage.acquisitions()
    try:
        first = service.materialize_to_identity(placement, "bob")
        target = Path(first["path"])
        assert first["status"] == "complete"
        assert target.read_bytes() == src.read_bytes()
        assert os.stat(src).st_ino != os.stat(target).st_ino
        assert first["placement_id"] == service.storage.placement_for_path(target)["placement_id"]
        assert service.storage.entitlements() == before_entitlements
        assert service.storage.acquisitions() == before_acquisitions
        bob = [
            row for row in service.storage.catalog_entries()
            if row.get("identity_id") == "bob"
        ]
        assert len(bob) == 1
        assert bob[0]["metadata"]["transfer_kind"] == "local_copy"
        assert bob[0]["metadata"]["file_origin_identity_id"] == "alice"
        assert bob[0]["metadata"]["description"] == "Public description"
        for forbidden in ("source_credential", "rights", "owned"):
            assert forbidden not in bob[0]["metadata"]

        second = service.materialize_to_identity(placement, "bob")
        assert second["status"] == "unchanged"
        assert second["placement_id"] == first["placement_id"]
        assert second["path"] == first["path"]
        assert len([p for p in service.storage.placements()
                    if p.get("identity_id") == "bob"]) == 1
        assert len([row for row in service.storage.catalog_entries()
                    if row.get("identity_id") == "bob"]) == 1
        assert src.read_bytes() == target.read_bytes()
    finally:
        service.close()


def test_equal_bytes_belonging_to_bob_are_never_reused_or_relabelled(tmp_path):
    service = _service(tmp_path)
    src, placement = _source(service, tmp_path)
    owned = tmp_path / "EBooks" / "bob" / "local" / "Book.epub"
    owned.parent.mkdir(parents=True, exist_ok=True)
    owned.write_bytes(src.read_bytes())
    own_placement = _placement(
        service, owned, identity_id="bob", source_item_id="another-book",
    )
    service.storage.catalog_entry_put({
        "catalog_entry_id": "bob-existing", "artifact_id": "sha256:test",
        "placement_id": own_placement, "title": "Bob's independent book",
        "identity_id": "bob", "provider_id": "local",
        "item_id": "another-book", "local_state": "local",
        "metadata": {"path": str(owned), "rights": "own-provenance"},
    })
    try:
        result = service.materialize_to_identity(placement, "bob")
        target = Path(result["path"])
        assert target != owned
        assert target.name == "Book (2).epub"
        assert target.read_bytes() == src.read_bytes()
        assert os.stat(src).st_ino != os.stat(target).st_ino
        previous = service.storage.placement_get(own_placement)
        assert previous["source_item_id"] == "another-book"
        original = next(row for row in service.storage.catalog_entries()
                        if row.get("catalog_entry_id") == "bob-existing")
        assert original["title"] == "Bob's independent book"
        assert original["metadata"]["rights"] == "own-provenance"
        again = service.materialize_to_identity(placement, "bob")
        assert again["status"] == "unchanged"
        assert again["path"] == result["path"]
    finally:
        service.close()


def test_existing_legacy_hardlink_from_this_placement_is_repaired(tmp_path):
    service = _service(tmp_path)
    src, placement = _source(service, tmp_path)
    target = tmp_path / "EBooks" / "bob" / "local" / "Book.epub"
    target.parent.mkdir(parents=True, exist_ok=True)
    os.link(src, target)
    assert os.path.samefile(src, target)
    old_id = service.storage.placement_put({
        "artifact_id": "sha256:test", "path": str(target),
        "identity_id": "bob", "provider_id": "local",
        "source_item_id": "book", "state": "present",
        "metadata": {"materialized_from": placement},
    })
    try:
        result = service.materialize_to_identity(placement, "bob")
        assert result["status"] == "complete"
        assert Path(result["path"]) == target
        assert result["placement_id"] == old_id
        assert not os.path.samefile(src, target)
        assert target.read_bytes() == src.read_bytes()
        assert service.materialize_to_identity(placement, "bob")["status"] == "unchanged"
    finally:
        service.close()


def test_dangling_destination_symlink_is_rejected_without_external_write(tmp_path):
    service = _service(tmp_path)
    src, placement = _source(service, tmp_path)
    target = tmp_path / "EBooks" / "bob" / "local" / "Book.epub"
    target.parent.mkdir(parents=True, exist_ok=True)
    outside = tmp_path / "external.epub"
    target.symlink_to(outside)
    try:
        with pytest.raises(ProviderConfigurationError, match="symbolique"):
            service.materialize_to_identity(placement, "bob")
        assert target.is_symlink()
        assert not outside.exists()
        assert src.read_bytes() == b"book-content"
        assert not any(p.get("identity_id") == "bob"
                       for p in service.storage.placements())
    finally:
        service.close()


def test_parent_directory_symlink_is_rejected_before_mkdir_or_copy(tmp_path):
    service = _service(tmp_path)
    src, placement = _source(service, tmp_path)
    outside = tmp_path / "external-dir"
    outside.mkdir()
    target_identity = tmp_path / "EBooks" / "bob"
    target_identity.symlink_to(outside, target_is_directory=True)
    try:
        with pytest.raises(ProviderConfigurationError, match="symbolique"):
            service.materialize_to_identity(placement, "bob")
        assert not list(outside.iterdir())
        assert not any(p.get("identity_id") == "bob"
                       for p in service.storage.placements())
    finally:
        service.close()


def test_bundle_members_are_copied_not_hardlinked(tmp_path):
    service = _service(tmp_path)
    bundle = tmp_path / "EBooks" / "alice" / "local" / "Audio"
    bundle.mkdir(parents=True)
    (bundle / "track.mp3").write_bytes(b"audio-data")
    (bundle / "play.m3u8").write_text("#EXTM3U\ntrack.mp3\n", encoding="utf-8")
    write_bundle_manifest(bundle, title="Audio", playlist="play.m3u8")
    placement = _placement(service, bundle, artifact_id="sha256:bundle",
                           source_item_id="audio")
    try:
        result = service.materialize_to_identity(placement, "bob")
        target = Path(result["path"])
        assert result["status"] == "complete"
        assert (target / "track.mp3").read_bytes() == b"audio-data"
        assert os.stat(target / "track.mp3").st_ino != os.stat(bundle / "track.mp3").st_ino
        assert service.materialize_to_identity(placement, "bob")["status"] == "unchanged"
    finally:
        service.close()
