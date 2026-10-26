"""#3185: explicit filename normalization must never follow replaced sources.

Only synthetic local files, metadata and identity scopes.
"""
from __future__ import annotations

import os

import pytest

from cifereca.artifact_runtime import LibraryMaintenanceBrokerImpl
from cifereca.plugin_api import Conflict, LibraryMutationRequest
from tests.test_core041_artifact_authority import Control, make_service


def _case(tmp_path):
    service, _, placement_id = make_service(tmp_path)
    old = tmp_path / "library" / "Book.epub"
    new = tmp_path / "library" / "Author - Book.epub"
    broker = LibraryMaintenanceBrokerImpl(service)
    plan = broker.plan(LibraryMutationRequest(
        "normalize_filename", placement_id=placement_id,
    ))
    return service, placement_id, old, new, broker, plan


def _assert_rejected(service, placement_id, old, new, broker, plan):
    with pytest.raises(Conflict) as caught:
        broker.run(plan, Control())
    assert caught.value.code == "library_filename_conflict"
    assert not new.exists()
    assert service.storage.placement_get(placement_id)["path"] == str(old)
    assert all(
        row["metadata"].get("path") == str(old)
        for row in service.storage.catalog_entries()
        if row["placement_id"] == placement_id
    )


def test_replaced_file_symlink_cannot_rename_external_source(tmp_path):
    service, placement_id, old, new, broker, plan = _case(tmp_path)
    outside = tmp_path / "PRIVATE-external.epub"
    outside.write_bytes(b"external-not-owned")
    old.unlink()
    old.symlink_to(outside)

    _assert_rejected(service, placement_id, old, new, broker, plan)

    assert old.is_symlink()
    assert outside.read_bytes() == b"external-not-owned"
    assert not (tmp_path / "Author - PRIVATE-external.epub").exists()


def test_replaced_parent_symlink_cannot_rename_external_directory(tmp_path):
    service, placement_id, old, new, broker, plan = _case(tmp_path)
    original = tmp_path / "library"
    moved_original = tmp_path / "saved-library"
    original.rename(moved_original)
    outside = tmp_path / "PRIVATE-external-directory"
    outside.mkdir()
    (outside / "Book.epub").write_bytes(b"external-not-owned")
    original.symlink_to(outside, target_is_directory=True)

    _assert_rejected(service, placement_id, old, new, broker, plan)

    assert (outside / "Book.epub").read_bytes() == b"external-not-owned"
    assert not (outside / "Author - Book.epub").exists()
    assert (moved_original / "Book.epub").read_bytes() == b"book-data"


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO unavailable")
def test_special_file_replacing_book_cannot_be_normalized(tmp_path):
    service, placement_id, old, new, broker, plan = _case(tmp_path)
    old.unlink()
    os.mkfifo(old)

    _assert_rejected(service, placement_id, old, new, broker, plan)

    assert old.exists()
