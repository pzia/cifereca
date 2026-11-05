"""#2847: a stale SQLite 'present' must not become an accessible recent book.

Real Storage/LibraryService plus synthetic files only. No network or mutation.
"""
from __future__ import annotations

import pytest

from tests.test_core_p0_2797_recent_catalog_owner_binding import _present
from tests.test_core_library_placement_identity_scope import _service


@pytest.mark.parametrize("change", ("missing", "dangling_symlink", "foreign_symlink", "directory"))
def test_recent_view_excludes_unavailable_present_placement_without_sql_mutation(
    tmp_path, change,
):
    service = _service(tmp_path)
    try:
        pid, artifact, path = _present(service, tmp_path, owner="alice")
        assert [row["placement_id"] for row in service.recent_acquisitions(
            identity_id="alice"
        )] == [pid]
        original = service.storage.placements()
        path.unlink()
        if change == "dangling_symlink":
            path.symlink_to(path.parent / "missing.pdf")
        elif change == "foreign_symlink":
            foreign = tmp_path / "library" / "bob" / "private.epub"
            foreign.parent.mkdir(parents=True, exist_ok=True)
            foreign.write_bytes(b"synthetic private bytes")
            path.symlink_to(foreign)
        elif change == "directory":
            path.mkdir()
        assert service.recent_acquisitions(identity_id="alice") == []
        assert service.storage.placements() == original
        assert service.storage.artifact_get(artifact) is not None
        # Restoring the original path makes the book visible again; SQLite
        # was deliberately never rewritten by this passive projection.
        if path.is_symlink():
            path.unlink()
        elif path.is_dir():
            path.rmdir()
        path.write_bytes(b"synthetic offline private book bytes")
        assert [row["placement_id"] for row in service.recent_acquisitions(
            identity_id="alice"
        )] == [pid]
    finally:
        service.close()


def test_recent_ignores_foreign_profile_even_if_sqlite_claims_owned_path(tmp_path):
    service = _service(tmp_path)
    try:
        pid, _artifact, path = _present(service, tmp_path, owner="bob")
        before = service.storage.placements()
        # Corrupt SQLite's declared owner while keeping a real Bob file.
        with service.storage._lock, service.storage._connect() as db:
            db.execute(
                "UPDATE placements SET identity_id='alice' WHERE placement_id=?",
                (pid,),
            )
        corrupted = service.storage.placements()
        assert corrupted != before
        assert service.recent_acquisitions(identity_id="alice") == []
        assert service.storage.placements() == corrupted
        assert path.read_bytes() == b"synthetic offline private book bytes"
    finally:
        service.close()
