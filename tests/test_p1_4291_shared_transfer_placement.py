"""#4291/Q025: every *new* inter-identity send uses recipient/shared.

Real LibraryService, SQLite and local bytes in tmp_path. Never contact Paris,
BnF, Kobo, ADEPT or an account. No implicit rights, copy migration or Sync.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from cifereca.config import Config
from cifereca.diagnostics import Diagnostics
from cifereca.providers import ProviderConfigurationError
from cifereca.services import LibraryService
from cifereca.storage import Storage
from cifereca.core.transfer_storage_policy import (
    TRANSFER_STORAGE_CLASS, directory_for_storage_class,
)
from tests.test_core0925_open_destinations import _service


def source(service, root: Path, provider: str | None, *, item="fixture", data=b"one"):
    folder = provider or "local"
    path = root / "alice" / folder / "Book.epub"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    pid = service.storage.placement_put({
        "artifact_id": "sha256:legacy", "path": str(path),
        "identity_id": "alice", "provider_id": provider,
        "source_item_id": item, "state": "present", "metadata": {},
    })
    service.storage.catalog_entry_put({
        "catalog_entry_id": "donor-" + item, "artifact_id": "sha256:legacy",
        "placement_id": pid, "identity_id": "alice",
        "provider_id": provider, "item_id": item, "title": "Fixture",
        "media_format": "epub", "local_state": "local",
        "metadata": {
            "rights": "DONOR_PRIVATE_RIGHT", "source_credential": "PRIVATE",
            "description": "Public description",
        },
    })
    return path, pid


@pytest.mark.parametrize("provider", [
    "bnf-pret", "paris", "kobo", "local", None,
])
def test_new_send_uses_recipient_shared_not_source_provider(tmp_path, provider):
    service = _service(tmp_path)
    root = tmp_path / "EBooks"
    try:
        before_rights = service.storage.entitlements()
        before_acq = service.storage.acquisitions()
        a, pid = source(service, root, provider)
        result = service.materialize_to_identity(pid, "bob")
        b = Path(result["path"])
        assert result["status"] == "complete"
        assert b.parent == root / "bob" / "shared"
        assert b.read_bytes() == a.read_bytes()
        assert os.stat(a).st_ino != os.stat(b).st_ino
        bob = service.storage.placement_get(result["placement_id"])
        assert bob["identity_id"] == "bob"
        assert bob["provider_id"] == (provider or "shared")
        assert bob["metadata"]["storage_class"] == "transfer"
        assert bob["metadata"]["transfer_kind"] == "local_copy"
        assert bob["metadata"]["file_origin_identity_id"] == "alice"
        catalog, = [
            row for row in service.storage.catalog_entries()
            if row.get("placement_id") == result["placement_id"]
        ]
        assert catalog["metadata"]["storage_class"] == "transfer"
        assert catalog["metadata"]["description"] == "Public description"
        assert catalog["provider_id"] == (provider or "shared")
        assert "rights" not in catalog["metadata"]
        assert "source_credential" not in catalog["metadata"]
        assert service.storage.entitlements() == before_rights
        assert service.storage.acquisitions() == before_acq
        repeated = service.materialize_to_identity(pid, "bob")
        assert repeated["status"] == "unchanged"
        assert repeated["placement_id"] == result["placement_id"]
        assert repeated["path"] == result["path"]
    finally:
        service.close()


def test_storage_class_is_closed_and_independent_of_supplier_labels():
    assert TRANSFER_STORAGE_CLASS == "transfer"
    assert directory_for_storage_class("transfer") == "shared"
    for rejected in ("paris", "kobo", "bnf-pret", "other", "", None, True):
        with pytest.raises(ValueError, match="unsupported storage class"):
            directory_for_storage_class(rejected)


def test_existing_independent_shared_file_is_never_adopted_or_overwritten(tmp_path):
    service = _service(tmp_path)
    root = tmp_path / "EBooks"
    try:
        a, pid = source(service, root, "bnf-pret")
        existing = root / "bob" / "shared" / "Book.epub"
        existing.parent.mkdir(parents=True, exist_ok=True)
        existing.write_bytes(b"Bob's preexisting content")
        own_id = service.storage.placement_put({
            "artifact_id": "sha256:bob", "path": str(existing),
            "identity_id": "bob", "provider_id": "local",
            "source_item_id": "own-book", "state": "present", "metadata": {
                "private": "not transfer evidence",
            },
        })
        receipt = service.materialize_to_identity(pid, "bob")
        b = Path(receipt["path"])
        assert b == existing.with_name("Book (2).epub")
        assert b.read_bytes() == a.read_bytes()
        assert existing.read_bytes() == b"Bob's preexisting content"
        assert service.storage.placement_get(own_id)["metadata"] == {
            "private": "not transfer evidence",
        }
        assert service.materialize_to_identity(pid, "bob")["path"] == str(b)
        assert len([row for row in service.storage.placements()
                    if row["identity_id"] == "bob"]) == 2
    finally:
        service.close()


def test_verified_historical_provider_directory_copy_stays_in_place(tmp_path):
    service = _service(tmp_path)
    root = tmp_path / "EBooks"
    try:
        a, pid = source(service, root, "bnf-pret")
        old = root / "bob" / "bnf-pret" / "Book.epub"
        old.parent.mkdir(parents=True, exist_ok=True)
        old.write_bytes(a.read_bytes())
        prior = service.storage.placement_put({
            "artifact_id": "sha256:legacy", "path": str(old),
            "identity_id": "bob", "provider_id": "bnf-pret",
            "source_item_id": "fixture", "state": "present",
            "metadata": {
                "materialized_from": pid, "transfer_kind": "local_copy",
                "file_origin_identity_id": "alice",
            },
        })
        service.storage.catalog_entry_put({
            "catalog_entry_id": "legacy-b", "artifact_id": "sha256:legacy",
            "placement_id": prior, "identity_id": "bob",
            "provider_id": "bnf-pret", "item_id": "fixture",
            "title": "Old copy", "local_state": "local",
            "metadata": {"path": str(old)},
        })
        repeated = service.materialize_to_identity(pid, "bob")
        assert repeated["status"] == "unchanged"
        assert repeated["path"] == str(old)
        assert repeated["placement_id"] == prior
        assert not (root / "bob" / "shared" / "Book.epub").exists()
        assert old.read_bytes() == a.read_bytes()
    finally:
        service.close()


def test_unproved_historical_provider_copy_is_never_claimed(tmp_path):
    service = _service(tmp_path)
    root = tmp_path / "EBooks"
    try:
        a, pid = source(service, root, "paris")
        old = root / "bob" / "paris" / "Book.epub"
        old.parent.mkdir(parents=True, exist_ok=True)
        old.write_bytes(a.read_bytes())
        old_id = service.storage.placement_put({
            "artifact_id": "sha256:legacy", "path": str(old),
            "identity_id": "bob", "provider_id": "paris",
            "source_item_id": "fixture", "state": "present",
            "metadata": {"materialized_from": pid},
        })
        result = service.materialize_to_identity(pid, "bob")
        b = Path(result["path"])
        assert b == root / "bob" / "shared" / "Book.epub"
        assert b.read_bytes() == old.read_bytes()
        assert result["placement_id"] != old_id
        assert service.storage.placement_get(old_id)["metadata"] == {
            "materialized_from": pid,
        }
    finally:
        service.close()


def service_three(tmp_path):
    config = Config(tmp_path / "config.json")
    config.set("library.directory", str(tmp_path / "EBooks"))
    config.set("providers.accounts", [])
    config.set("identities", [
        {"id": name, "label": name.capitalize(), "directory_name": name}
        for name in ("alice", "bob", "charlie")
    ])
    return LibraryService(
        config, Storage(tmp_path / "db.sqlite3"), Diagnostics(level="detailed"),
    )


def test_two_hops_use_each_recipient_shared_without_cross_identity_folder(tmp_path):
    service = service_three(tmp_path)
    root = tmp_path / "EBooks"
    try:
        a, pid = source(service, root, "kobo")
        first = service.materialize_to_identity(pid, "bob")
        second = service.materialize_to_identity(first["placement_id"], "charlie")
        b, c = Path(first["path"]), Path(second["path"])
        assert b.parent == root / "bob" / "shared"
        assert c.parent == root / "charlie" / "shared"
        assert a.read_bytes() == b.read_bytes() == c.read_bytes()
        assert len({os.stat(p).st_ino for p in (a, b, c)}) == 3
        assert service.storage.entitlements() == []
        assert service.storage.acquisitions() == []
        bob = service.storage.placement_get(first["placement_id"])
        charlie = service.storage.placement_get(second["placement_id"])
        assert bob["metadata"]["file_origin_identity_id"] == "alice"
        assert charlie["metadata"]["file_origin_identity_id"] == "bob"
        assert bob["provider_id"] == charlie["provider_id"] == "kobo"
        assert service.materialize_to_identity(pid, "bob")["status"] == "unchanged"
        assert service.materialize_to_identity(first["placement_id"], "charlie")["status"] == "unchanged"
    finally:
        service.close()


def test_shared_destination_symlink_is_rejected_without_external_writes(tmp_path):
    service = _service(tmp_path)
    root = tmp_path / "EBooks"
    try:
        a, pid = source(service, root, "paris")
        external = tmp_path / "unrelated"
        external.mkdir()
        owner = root / "bob"
        owner.mkdir(parents=True)
        (owner / "shared").symlink_to(external, target_is_directory=True)
        with pytest.raises(ProviderConfigurationError, match="symbolique"):
            service.materialize_to_identity(pid, "bob")
        assert list(external.iterdir()) == []
        assert a.read_bytes() == b"one"
        assert not any(row["identity_id"] == "bob"
                       for row in service.storage.placements())
    finally:
        service.close()
