"""P0 #3196: the old file-moving Kobo facade cannot bypass supervised Core.

Synthetic SQLite and filesystem only; no user files, provider I/O or migration.
"""
from __future__ import annotations

import hashlib

import pytest

from cifereca.providers import ProviderConfigurationError
from tests.test_kobo_backup_guard import _service, _write_epub


def test_legacy_flat_migration_facade_fails_before_overwrite_or_source_unlink(
    tmp_path,
):
    service = _service(tmp_path)
    try:
        root = service.library_dir()
        source = root / "old-flat.epub"
        _write_epub(source, b"old-flat")
        destination = root / "alice" / "kobo" / source.name
        _write_epub(destination, b"foreign-preexisting-target")
        before_source = source.read_bytes()
        before_destination = destination.read_bytes()
        service.storage.backup_put({
            "provider_id": "kobo", "credential_id": "vault-1",
            "account_id": "account", "identity_id": "alice",
            "item_id": "old-edition", "revision_id": "r1",
            "title": "Historical Kobo", "format": "EPUB",
            "status": "complete", "local_path": str(source),
            "size": len(before_source),
            "sha256": hashlib.sha256(before_source).hexdigest(),
        })
        before_rows = {
            "backup": service.storage.backup_list("kobo"),
            "placements": service.storage.placements(),
            "catalogue": service.storage.catalog_entries(),
            "entitlements": service.storage.entitlements(),
            "acquisitions": service.storage.acquisitions(),
        }
        with pytest.raises(
            ProviderConfigurationError,
            match="legacy_kobo_reclassification_requires_supervised_migration",
        ):
            service._migrate_legacy_flat_kobo_files(
                service.identity_by_id("alice"),
            )
        assert source.read_bytes() == before_source
        assert destination.read_bytes() == before_destination
        assert service.storage.backup_list("kobo") == before_rows["backup"]
        assert service.storage.placements() == before_rows["placements"]
        assert service.storage.catalog_entries() == before_rows["catalogue"]
        assert service.storage.entitlements() == before_rows["entitlements"]
        assert service.storage.acquisitions() == before_rows["acquisitions"]
    finally:
        service.close()


def test_legacy_flat_migration_facade_does_not_create_destination_without_rows(
    tmp_path,
):
    service = _service(tmp_path)
    try:
        target = service.library_dir() / "alice" / "kobo" / "legacy.epub"
        assert not target.exists()
        with pytest.raises(
            ProviderConfigurationError,
            match="legacy_kobo_reclassification_requires_supervised_migration",
        ):
            service._migrate_legacy_flat_kobo_files(
                service.identity_by_id("alice"),
            )
        assert not target.exists()
        assert service.storage.backup_list("kobo") == []
    finally:
        service.close()
