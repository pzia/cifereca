"""P0 #2797: catalogue metadata cannot cross the owning Placement profile.

Offline SQLite and synthetic files. A placement ID alone is not a licence
to read a different profile's title, author, cover, or source item.
"""
import hashlib

import pytest

from tests.test_core_library_placement_identity_scope import _service


PRIVATE = "BOB_PRIVATE_TITLE_CATALOG"
PRIVATE_URL = "https://bob.invalid/PRIVATE_COVER"


def _present(service, tmp_path, *, owner="alice"):
    raw = b"synthetic offline private book bytes"
    digest = hashlib.sha256(raw).hexdigest()
    artifact_id = "sha256:" + digest
    path = tmp_path / "library" / owner / "Shared.epub"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    service.storage.artifact_put({
        "artifact_id": artifact_id, "sha256": digest,
        "size": len(raw), "media_format": "epub",
    })
    placement_id = service.storage.placement_put({
        "artifact_id": artifact_id, "path": str(path),
        "identity_id": owner, "provider_id": "local",
        "source_item_id": "local-edition", "state": "present",
    })
    return placement_id, artifact_id, path


def _catalog(service, placement_id, artifact_id, *, entry_id,
             identity, title="Owner book", cover=None):
    service.storage.catalog_entry_put({
        "catalog_entry_id": entry_id, "artifact_id": artifact_id,
        "placement_id": placement_id, "identity_id": identity,
        "provider_id": "kobo", "item_id": "PRIVATE_EDITION" if cover == PRIVATE_URL else "local-edition",
        "title": title, "author": "Private author" if cover == PRIVATE_URL else "Local author",
        "media_format": "epub",
        "metadata": {"cover_url": cover} if cover else {},
        "local_state": "local",
    })


def test_foreign_catalogue_row_cannot_decorate_local_placement(tmp_path):
    service = _service(tmp_path)
    try:
        pid, artifact, path = _present(service, tmp_path)
        _catalog(service, pid, artifact, entry_id="foreign", identity="bob",
                 title=PRIVATE, cover=PRIVATE_URL)
        rows = service.recent_acquisitions(identity_id="alice")
        assert len(rows) == 1 and rows[0]["placement_id"] == pid
        assert rows[0]["title"] == path.stem
        assert rows[0]["author"] is None
        assert rows[0]["provider_id"] == "local"
        assert rows[0]["item_id"] == "local-edition"
        assert rows[0]["thumbnail_url"] is None
        assert rows[0]["metadata"]["cover_resolution"]["status"] == "missing"
        assert PRIVATE not in repr(rows) and PRIVATE_URL not in repr(rows)
    finally:
        service.close()


@pytest.mark.parametrize("owner_first", [False, True])
def test_real_owner_metadata_wins_over_foreign_row_regardless_of_order(
    tmp_path, owner_first,
):
    service = _service(tmp_path)
    try:
        pid, artifact, _ = _present(service, tmp_path)
        local = dict(
            entry_id="local", identity="alice",
            title="Alice valid book", cover="https://alice.invalid/valid.jpg",
        )
        foreign = dict(
            entry_id="foreign", identity="bob",
            title=PRIVATE, cover=PRIVATE_URL,
        )
        for record in ((local, foreign) if owner_first else (foreign, local)):
            _catalog(service, pid, artifact, **record)
        rows = service.recent_acquisitions(identity_id="alice")
        assert len(rows) == 1
        assert rows[0]["title"] == "Alice valid book"
        assert rows[0]["thumbnail_url"] == "https://alice.invalid/valid.jpg"
        assert rows[0]["author"] == "Local author"
        assert PRIVATE not in repr(rows) and PRIVATE_URL not in repr(rows)
    finally:
        service.close()


def test_two_matching_catalogue_rows_are_ambiguous_not_last_writer_wins(tmp_path):
    service = _service(tmp_path)
    try:
        pid, artifact, path = _present(service, tmp_path)
        _catalog(service, pid, artifact, entry_id="owner-1",
                 identity="alice", title="Owner one")
        _catalog(service, pid, artifact, entry_id="owner-2",
                 identity="alice", title="Owner two")
        rows = service.recent_acquisitions(identity_id="alice")
        assert len(rows) == 1 and rows[0]["title"] == path.stem
        assert rows[0]["author"] is None
    finally:
        service.close()


def test_catalogue_row_of_different_artifact_cannot_decorate_placement(tmp_path):
    service = _service(tmp_path)
    try:
        pid, _artifact, path = _present(service, tmp_path)
        _catalog(service, pid, "sha256:" + "0" * 64, entry_id="wrong-artifact",
                 identity="alice", title=PRIVATE, cover=PRIVATE_URL)
        rows = service.recent_acquisitions(identity_id="alice")
        assert len(rows) == 1 and rows[0]["title"] == path.stem
        assert rows[0]["thumbnail_url"] is None
        assert PRIVATE not in repr(rows) and PRIVATE_URL not in repr(rows)
    finally:
        service.close()


@pytest.mark.parametrize("owner,allowed", [("alice", True), ("bob", False)])
def test_unscoped_v07_catalogue_visible_only_to_default_identity(tmp_path, owner, allowed):
    service = _service(tmp_path)
    try:
        pid, artifact, path = _present(service, tmp_path, owner=owner)
        _catalog(service, pid, artifact, entry_id="legacy",
                 identity=None, title="Legacy descriptive title")
        rows = service.recent_acquisitions(identity_id=owner)
        assert len(rows) == 1 and rows[0]["placement_id"] == pid
        assert rows[0]["title"] == ("Legacy descriptive title" if allowed else path.stem)
    finally:
        service.close()
