"""P0 #2797: a path layout is not a provider, entitlement or import receipt.

The three historical profile folders and an arbitrary provider-named folder are
physical organization only. All facts use a real temporary SQLite DB and local
synthetic PDF bytes, without provider, DRM or network I/O.
"""
from __future__ import annotations

from cifereca.catalog import LocalCatalog
from cifereca.storage import Storage


def _file(root, category, filename="book.pdf"):
    path = root / "reader" / category / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"%PDF-1.7\nSynthetic offline content\n")
    return path


def _environment(tmp_path):
    root = tmp_path / "EBooks"
    root.mkdir()
    store = Storage(tmp_path / "library.sqlite")
    store.identity_put({
        "identity_id": "reader",
        "directory_name": "reader",
        "label": "Reader",
    })
    return store, root


def test_manual_acsm_import_other_and_provider_named_folders_do_not_grant_source(
    tmp_path,
):
    store, root = _environment(tmp_path)
    categories = ("manual", "acsm-import", "other", "numilog", "kobo")
    paths = [_file(root, category) for category in categories]
    originals = {str(path): path.read_bytes() for path in paths}
    try:
        catalog = LocalCatalog(store, root)
        for _ in range(2):
            result = catalog.scan()
            assert result.cataloged == len(paths)
            assert result.invalid == 0
            assert len(store.placements()) == len(paths)
            for path in paths:
                placement = store.placement_for_path(path)
                assert placement is not None
                assert placement["state"] == "present"
                assert placement["identity_id"] == "reader"
                assert placement["provider_id"] is None
                assert placement["source_item_id"] is None
                entry = next(
                    entry for entry in store.catalog_entries()
                    if entry["placement_id"] == placement["placement_id"]
                )
                assert entry["provider_id"] is None
                assert entry["item_id"] is None
            assert store.entitlements() == []
            assert store.acquisitions() == []
            assert store.backup_list() == []
        assert {str(path): path.read_bytes() for path in paths} == originals
    finally:
        store.close()


def test_explicit_indexed_source_in_other_folder_survives_passive_rescan(tmp_path):
    store, root = _environment(tmp_path)
    path = _file(root, "other", "exact-public-book.pdf")
    try:
        catalog = LocalCatalog(store, root)
        result = catalog.index_path(
            path, identity_id="reader", provider_id="attested-catalogue",
            item_id="stable-edition-123",
        )
        assert result["status"] == "indexed"
        for _ in range(2):
            result = catalog.scan()
            assert result.invalid == 0
            placement = store.placement_for_path(path)
            assert placement["identity_id"] == "reader"
            assert placement["provider_id"] == "attested-catalogue"
            assert placement["source_item_id"] == "stable-edition-123"
            entry = next(
                item for item in store.catalog_entries()
                if item["placement_id"] == placement["placement_id"]
            )
            assert entry["provider_id"] == "attested-catalogue"
            assert entry["item_id"] == "stable-edition-123"
        assert store.entitlements() == []
    finally:
        store.close()


def test_exact_historical_receipt_still_attests_provider_in_other_folder(tmp_path):
    store, root = _environment(tmp_path)
    path = _file(root, "other", "historical.pdf")
    try:
        store.backup_put({
            "provider_id": "legacy-attested-seller",
            "credential_id": "account-a",
            "account_id": "borrower-a",
            "identity_id": "reader",
            "item_id": "edition-opaque",
            "entitlement_type": "owned",
            "status": "complete",
            "local_path": str(path),
            "title": "Historical exact receipt",
            "format": "pdf",
        })
        for _ in range(2):
            result = LocalCatalog(store, root).scan()
            assert result.invalid == 0
            placement = store.placement_for_path(path)
            assert placement["provider_id"] == "legacy-attested-seller"
            assert placement["source_item_id"] == "edition-opaque"
            assert placement["identity_id"] == "reader"
        assert len(store.entitlements()) == 1
        assert store.entitlements()[0]["provider_id"] == "legacy-attested-seller"
        assert len(store.acquisitions()) == 1
    finally:
        store.close()


def test_targeted_index_cannot_inherit_foreign_legacy_receipt_by_path(tmp_path):
    store, root = _environment(tmp_path)
    path = _file(root, "other", "historical-from-other-identity.pdf")
    try:
        # Simulate a partially migrated 0.7 database: an old Kobo projection
        # points at a file currently physically under another identity.
        # The row is intentionally not mirrored to canonical placements.
        with store._lock, store._connect() as db:
            db.execute(
                "INSERT INTO backup_items("
                "provider_id,credential_id,item_id,identity_id,account_id,"
                "local_path,title,status,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                ("foreign-provider", "foreign-credential", "other-edition",
                 "other-reader", "foreign-borrower", str(path),
                 "Foreign historical receipt", "complete", 1_730_000_000),
            )
        assert LocalCatalog(store, root).index_path(path)["status"] == "indexed"
        placement = store.placement_for_path(path)
        assert placement is not None
        assert placement["identity_id"] == "reader"
        assert placement["provider_id"] is None
        assert placement["source_item_id"] is None
        linked = [
            item for item in store.catalog_entries()
            if item["placement_id"] == placement["placement_id"]
        ]
        assert len(linked) == 1
        assert linked[0]["provider_id"] is None
        assert linked[0]["item_id"] is None
        assert len(store.backup_list()) == 1
        assert store.entitlements() == []
    finally:
        store.close()
