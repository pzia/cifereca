"""#3185: renaming an indexed file cannot carry a stale artifact hash.

All files and books are synthetic; no real library and no provider I/O.
"""
from __future__ import annotations

import os

import pytest

from cifereca.artifact_runtime import LibraryMaintenanceBrokerImpl
from cifereca.plugin_api import Conflict, LibraryMutationRequest
from tests.test_core041_artifact_authority import Control, make_service


def _case(tmp_path):
    service, source, placement_id = make_service(tmp_path)
    old = tmp_path / "library" / "Book.epub"
    new = tmp_path / "library" / "Author - Book.epub"
    broker = LibraryMaintenanceBrokerImpl(service)
    plan = broker.plan(LibraryMutationRequest(
        "normalize_filename", placement_id=placement_id,
    ))
    return service, source, placement_id, old, new, broker, plan


def _refused(service, placement_id, old, new, broker, plan):
    with pytest.raises(Conflict) as caught:
        broker.run(plan, Control())
    assert caught.value.code == "library_filename_conflict"
    assert not new.exists()
    assert service.storage.placement_get(placement_id)["path"] == str(old)
    assert all(
        row["metadata"].get("path") == str(old)
        for row in service.storage.catalog_entries()
        if row.get("placement_id") == placement_id
    )


@pytest.mark.parametrize("payload", [
    b"changedXX",  # same size as the original "book-data"
    b"changed-longer-than-original",
])
def test_modified_bytes_cannot_be_renamed_under_stale_artifact_id(
    tmp_path, payload,
):
    service, source, placement_id, old, new, broker, plan = _case(tmp_path)
    old.write_bytes(payload)

    _refused(service, placement_id, old, new, broker, plan)

    assert old.read_bytes() == payload
    assert service.storage.artifact_get(source.artifact_id)["sha256"] == source.sha256


def test_same_size_and_original_mtime_still_checks_fresh_digest(tmp_path):
    service, _, placement_id, old, new, broker, plan = _case(tmp_path)
    before = old.stat()
    modified = b"changedXX"
    assert len(modified) == before.st_size
    old.write_bytes(modified)
    os.utime(old, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert old.stat().st_mtime_ns == before.st_mtime_ns

    _refused(service, placement_id, old, new, broker, plan)


def test_missing_artifact_evidence_cannot_authorize_rename(tmp_path):
    service, source, placement_id, old, new, broker, plan = _case(tmp_path)
    with service.storage._lock, service.storage._connect() as db:
        db.execute("DELETE FROM artifacts WHERE artifact_id=?", (source.artifact_id,))

    _refused(service, placement_id, old, new, broker, plan)
    assert old.read_bytes() == b"book-data"


def test_inconsistent_stored_digest_cannot_authorize_rename(tmp_path):
    service, source, placement_id, old, new, broker, plan = _case(tmp_path)
    with service.storage._lock, service.storage._connect() as db:
        db.execute(
            "UPDATE artifacts SET sha256=? WHERE artifact_id=?",
            ("0" * 64, source.artifact_id),
        )

    _refused(service, placement_id, old, new, broker, plan)


def test_verified_original_normalizes_without_changing_artifact_proof(tmp_path):
    service, source, placement_id, old, new, broker, plan = _case(tmp_path)

    result = broker.run(plan, Control())

    assert result.ok and result.changed == 1
    assert new.read_bytes() == b"book-data"
    assert not old.exists()
    assert service.storage.placement_get(placement_id)["path"] == str(new)
    assert service.storage.placement_get(placement_id)["artifact_id"] == source.artifact_id
    assert service.storage.artifact_get(source.artifact_id)["sha256"] == source.sha256
