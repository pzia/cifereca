"""P0 #2789: a rename must not merge another identity's evidence.

Entirely synthetic files; no actual library, account or repairs.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from cifereca.artifact_runtime import LibraryMaintenanceBrokerImpl
from cifereca.plugin_api import Conflict, LibraryMutationRequest
from tests.test_core041_artifact_authority import Control, make_service


def _target(tmp_path: Path) -> Path:
    return tmp_path / "library" / "Author - Book.epub"


def _insert_destination(service, source, path, *, identity, provider="local",
                        item="book-1"):
    return service.storage.placement_put({
        "artifact_id": source.artifact_id,
        "path": str(path),
        "identity_id": identity,
        "provider_id": provider,
        "source_item_id": item,
        "state": "present",
        "metadata": {},
    })


@pytest.mark.parametrize(("identity", "provider", "item"), [
    ("other-identity", "local", "book-1"),
    ("id1", "other-provider", "book-1"),
    ("id1", "local", "other-edition"),
])
def test_same_sha_different_placement_scope_refuses_before_rename(
    tmp_path, identity, provider, item,
):
    service, source, old_id = make_service(tmp_path)
    old_path = tmp_path / "library" / "Book.epub"
    target_path = _target(tmp_path)
    target_path.write_bytes(old_path.read_bytes())  # Same SHA is insufficient.
    target_id = _insert_destination(
        service, source, target_path,
        identity=identity, provider=provider, item=item,
    )
    broker = LibraryMaintenanceBrokerImpl(service)
    plan = broker.plan(LibraryMutationRequest(
        "normalize_filename", placement_id=old_id,
    ))
    original = old_path.read_bytes(), target_path.read_bytes()
    with pytest.raises(Conflict) as caught:
        broker.run(plan, Control())
    assert caught.value.code == "library_filename_conflict"
    assert (old_path.read_bytes(), target_path.read_bytes()) == original
    assert service.storage.placement_get(old_id)["path"] == str(old_path)
    assert service.storage.placement_get(target_id)["path"] == str(target_path)
    with pytest.raises(ValueError, match="relocate_scope_conflict"):
        service.storage.relocate_local_path(
            old_path, target_path, identity_id="id1", provider_id="local",
        )
    assert service.storage.placement_get(old_id)["path"] == str(old_path)
    assert service.storage.placement_get(target_id)["path"] == str(target_path)


def test_untracked_destination_same_sha_is_not_overwritten(tmp_path):
    service, source, old_id = make_service(tmp_path)
    old_path = tmp_path / "library" / "Book.epub"
    target_path = _target(tmp_path)
    target_path.write_bytes(old_path.read_bytes())
    broker = LibraryMaintenanceBrokerImpl(service)
    plan = broker.plan(LibraryMutationRequest(
        "normalize_filename", placement_id=old_id,
    ))
    with pytest.raises(Conflict) as caught:
        broker.run(plan, Control())
    assert caught.value.code == "library_filename_conflict"
    assert old_path.exists() and target_path.exists()
    assert service.storage.placement_get(old_id)["path"] == str(old_path)


def test_same_identity_provider_artifact_and_book_can_merge_after_validation(tmp_path):
    service, source, old_id = make_service(tmp_path)
    old_path = tmp_path / "library" / "Book.epub"
    target_path = _target(tmp_path)
    target_path.write_bytes(old_path.read_bytes())
    target_id = _insert_destination(
        service, source, target_path, identity="id1",
    )
    broker = LibraryMaintenanceBrokerImpl(service)
    plan = broker.plan(LibraryMutationRequest(
        "normalize_filename", placement_id=old_id,
    ))
    result = broker.run(plan, Control())
    assert result.ok and result.changed == 1
    assert not old_path.exists() and target_path.read_bytes() == b"book-data"
    assert service.storage.placement_get(old_id) is None
    assert service.storage.placement_get(target_id)["identity_id"] == "id1"
    assert all(
        row.get("placement_id") != old_id
        for row in service.storage.catalog_entries()
    )
