"""#3171/#2797: target profile never borrows a foreign directory's placement."""
from __future__ import annotations

import pytest

from cifereca.catalog import LocalCatalog
from cifereca.plugin_api import write_bundle_manifest
from cifereca.storage import Storage


@pytest.fixture
def library(tmp_path):
    root = tmp_path / "EBooks"
    root.mkdir()
    store = Storage(tmp_path / "database.sqlite")
    for identity in ("alice", "bob"):
        store.identity_put({
            "identity_id": identity, "directory_name": identity,
            "label": identity.capitalize(),
        })
    try:
        yield LocalCatalog(store, root), store, root
    finally:
        store.close()


def pdf(root, *parts):
    path = root.joinpath(*parts)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"%PDF-1.7\nSynthetic test bytes\n")
    return path


@pytest.mark.parametrize("parts", [
    ("alice", "numilog", "new.pdf"),
    ("alice", "new-at-profile-root.pdf"),
])
def test_new_file_in_alice_cannot_be_indexed_as_bob_without_existing_placement(library, parts):
    catalog, store, root = library
    path = pdf(root, *parts)
    before = path.read_bytes()
    result = catalog.index_path(
        path, identity_id="bob", provider_id="numilog", item_id="right-1",
    )
    assert result["status"] == "invalid"
    assert result["reason"] == "placement_scope_conflict"
    assert store.placement_for_path(path) is None
    assert store.catalog_entries() == []
    assert store.artifacts() == []
    assert path.read_bytes() == before


def test_new_bundle_in_alice_cannot_be_indexed_as_bob(library):
    catalog, store, root = library
    bundle = root / "alice" / "audio" / "recording"
    bundle.mkdir(parents=True)
    (bundle / "track.mp3").write_bytes(b"synthetic audio")
    write_bundle_manifest(bundle, title="Recorded public-domain story")
    result = catalog.index_path(bundle, identity_id="bob")
    assert result["status"] == "invalid"
    assert result["reason"] == "placement_scope_conflict"
    assert store.placement_for_path(bundle) is None
    assert store.catalog_entries() == []


def test_direct_profile_file_has_owner_but_never_provider_inferred(library):
    catalog, store, root = library
    path = pdf(root, "alice", "direct.pdf")
    assert catalog.index_path(path)["status"] == "indexed"
    row = store.placement_for_path(path)
    assert row["identity_id"] == "alice"
    assert row["provider_id"] is None
    assert row["source_item_id"] is None
    for _ in range(2):
        catalog.scan()
        assert store.placement_for_path(path)["identity_id"] == "alice"
    assert store.entitlements() == []
    assert store.acquisitions() == []


def test_identical_bytes_are_two_distinct_profile_placements_not_shared_rights(library):
    catalog, store, root = library
    a = pdf(root, "alice", "other", "same.pdf")
    b = pdf(root, "bob", "other", "same.pdf")
    assert catalog.index_path(a)["status"] == "indexed"
    assert catalog.index_path(b)["status"] == "indexed"
    pa, pb = store.placement_for_path(a), store.placement_for_path(b)
    assert pa["identity_id"] == "alice"
    assert pb["identity_id"] == "bob"
    assert pa["artifact_id"] == pb["artifact_id"]
    assert pa["placement_id"] != pb["placement_id"]
    assert pa["provider_id"] is None and pb["provider_id"] is None
    assert store.entitlements() == []
    assert store.acquisitions() == []


def test_flat_legacy_and_unregistered_folder_still_do_not_infer_owner(library):
    catalog, store, root = library
    files = (pdf(root, "flat.pdf"), pdf(root, "unregistered", "file.pdf"))
    for path in files:
        assert catalog.index_path(path)["status"] == "indexed"
        row = store.placement_for_path(path)
        assert row["identity_id"] is None
        assert row["provider_id"] is None
