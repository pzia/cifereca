"""#2797: catalogue metadata cannot redirect an attested local Placement.

Real SQLite/LibraryService and synthetic files. Catalogues are descriptive:
only the scoped, openable Placement is authority for local_path.
"""
from __future__ import annotations

import pytest

from tests.test_core_p0_2797_recent_catalog_owner_binding import _present
from tests.test_core_library_placement_identity_scope import _service


@pytest.mark.parametrize("metadata_path", [
    "foreign_profile", "nonexistent", "foreign_symlink", "none",
])
def test_bound_library_card_uses_verified_placement_not_catalogue_path(
    tmp_path, metadata_path,
):
    service = _service(tmp_path)
    try:
        placement_id, artifact_id, alice_file = _present(
            service, tmp_path, owner="alice",
        )
        foreign = tmp_path / "library" / "bob" / "BOB_PRIVATE.epub"
        foreign.parent.mkdir(parents=True, exist_ok=True)
        foreign.write_bytes(b"synthetic private Bob bytes")
        descriptor = tmp_path / "library" / "alice" / "catalogue-path.epub"
        if metadata_path == "foreign_profile":
            supplied = str(foreign)
        elif metadata_path == "nonexistent":
            supplied = str(tmp_path / "library" / "alice" / "STALE.epub")
        elif metadata_path == "foreign_symlink":
            descriptor.symlink_to(foreign)
            supplied = str(descriptor)
        else:
            supplied = None

        raw = ({"path": supplied, "source": "legacy-descriptor"}
               if supplied is not None else {"source": "legacy-descriptor"})
        service.storage.catalog_entry_put({
            "catalog_entry_id": "owner",
            "artifact_id": artifact_id,
            "placement_id": placement_id,
            "identity_id": "alice",
            "provider_id": "local",
            "item_id": "alice-edition",
            "title": "Alice book",
            "local_state": "local",
            "media_format": "epub",
            "metadata": raw,
        })
        before = service.storage.catalog_entry_get("owner")
        rows = service.library_entries(identity_id="alice", scan=False)
        matching = [r for r in rows if r.get("artifact_id") == artifact_id]
        assert len(matching) == 1
        book = matching[0]
        assert book["local"] is True
        assert book["placement_id"] == placement_id
        assert book["local_path"] == str(alice_file)
        if supplied is not None:
            assert book["metadata"]["path"] == str(alice_file)
        else:
            assert "path" not in book["metadata"]
        assert book["metadata"]["source"] == "legacy-descriptor"
        if supplied is not None and supplied != str(alice_file):
            assert supplied not in repr(book)
        assert str(foreign) not in repr(book)
        assert service.storage.catalog_entry_get("owner") == before
        assert foreign.read_bytes() == b"synthetic private Bob bytes"
        assert alice_file.read_bytes() == b"synthetic offline private book bytes"
    finally:
        service.close()


def test_second_same_artifact_catalogue_cannot_replace_owners_path(tmp_path):
    service = _service(tmp_path)
    try:
        placement_id, artifact_id, alice_file = _present(
            service, tmp_path, owner="alice",
        )
        foreign = tmp_path / "library" / "bob" / "ANOTHER_BOB_PRIVATE.epub"
        foreign.parent.mkdir(parents=True, exist_ok=True)
        foreign.write_bytes(b"unrelated")
        for suffix, desc in (
            ("b-private", str(foreign)),
            ("a-owner", str(alice_file)),
        ):
            service.storage.catalog_entry_put({
                "catalog_entry_id": suffix,
                "artifact_id": artifact_id, "placement_id": placement_id,
                "identity_id": "alice", "provider_id": "local",
                "item_id": "alice-edition", "title": "Alice book",
                "local_state": "local", "media_format": "epub",
                "metadata": {"path": desc},
            })
        rows = service.library_entries(identity_id="alice", scan=False)
        matching = [r for r in rows if r.get("artifact_id") == artifact_id]
        assert len(matching) == 1
        assert matching[0]["local_path"] == str(alice_file)
        assert matching[0]["metadata"]["path"] == str(alice_file)
        assert str(foreign) not in repr(matching[0])
    finally:
        service.close()



@pytest.mark.parametrize("catalogue_path", ("foreign", "stale"))
def test_recent_acquisition_does_not_echo_foreign_catalogue_path_in_metadata(
    tmp_path, catalogue_path,
):
    service = _service(tmp_path)
    try:
        placement_id, artifact_id, alice_file = _present(
            service, tmp_path, owner="alice",
        )
        foreign = tmp_path / "library" / "bob" / "PRIVATE-DESCRIPTOR.epub"
        foreign.parent.mkdir(parents=True, exist_ok=True)
        foreign.write_bytes(b"private synthetic book")
        raw = (str(foreign) if catalogue_path == "foreign"
               else str(tmp_path / "library" / "alice" / "OLD-MISSING.epub"))
        service.storage.catalog_entry_put({
            "catalog_entry_id": "recent-owner",
            "artifact_id": artifact_id, "placement_id": placement_id,
            "identity_id": "alice", "provider_id": "local",
            "item_id": "alice-edition", "title": "Alice book",
            "local_state": "local", "media_format": "epub",
            "metadata": {"path": raw, "source": "old-catalog"},
        })
        before = service.storage.catalog_entry_get("recent-owner")
        rows = service.recent_acquisitions(identity_id="alice")
        assert len(rows) == 1
        assert rows[0]["placement_id"] == placement_id
        assert rows[0]["path"] == str(alice_file)
        assert rows[0]["metadata"]["path"] == str(alice_file)
        assert raw not in repr(rows)
        assert service.storage.catalog_entry_get("recent-owner") == before
        assert foreign.read_bytes() == b"private synthetic book"
    finally:
        service.close()
