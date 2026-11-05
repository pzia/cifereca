"""P0 #2797/#3185: local transfer may only copy owner-scoped catalogue metadata.

Tests the real LibraryService/SQLite transfer against corrupt/imported
cross-profile catalogue attachments. Files are disposable and offline.
"""
from pathlib import Path

import pytest

from tests.test_core0925_open_destinations import _placement, _service


PRIVATE_TITLE = "BOB_PRIVATE_CATALOG_TITLE"
PRIVATE_COVER = "https://example.invalid/BOB_PRIVATE_COVER"


def _catalog(service, pid, *, entry_id, owner, artifact="sha256:legacy",
             title="Valid Alice title", cover=None):
    service.storage.catalog_entry_put({
        "catalog_entry_id": entry_id,
        "artifact_id": artifact, "placement_id": pid,
        "identity_id": owner, "provider_id": "local",
        "item_id": "catalogue-item", "title": title,
        "author": "BOB_PRIVATE_AUTHOR" if cover else "Alice author",
        "media_format": "epub", "local_state": "local",
        "metadata": {"cover_url": cover} if cover else {},
    })


@pytest.mark.parametrize("bad_kind", ["foreign", "wrong_artifact", "unscoped_nondefault"])
def test_send_ignores_catalogue_not_owned_by_source_placement(tmp_path, bad_kind):
    service = _service(tmp_path)
    source_owner = "bob" if bad_kind == "unscoped_nondefault" else "alice"
    receiver = "alice" if source_owner == "bob" else "bob"
    source = tmp_path / "EBooks" / source_owner / "local" / "Book.epub"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"safe synthetic owned source")
    pid = _placement(service, source, identity_id=source_owner,
                     artifact_id="sha256:legacy", source_item_id="")
    foreign_owner = "bob" if bad_kind == "foreign" else (
        None if bad_kind == "unscoped_nondefault" else source_owner
    )
    artifact = "sha256:wrong" if bad_kind == "wrong_artifact" else "sha256:legacy"
    _catalog(service, pid, entry_id="private", owner=foreign_owner,
             artifact=artifact, title=PRIVATE_TITLE, cover=PRIVATE_COVER)
    try:
        result = service.materialize_to_identity(pid, receiver)
        assert Path(result["path"]).read_bytes() == source.read_bytes()
        projected = [r for r in service.storage.catalog_entries()
                     if r.get("identity_id") == receiver
                     and r.get("placement_id") == result["placement_id"]]
        assert len(projected) == 1
        assert projected[0]["title"] == source.stem
        assert projected[0]["author"] in (None, "")
        assert PRIVATE_TITLE not in repr(projected)
        assert PRIVATE_COVER not in repr(projected)
        assert "BOB_PRIVATE_AUTHOR" not in repr(projected)
        assert service.storage.entitlements() == []
    finally:
        service.close()


@pytest.mark.parametrize("owner_first", [True, False])
def test_send_uses_valid_owner_row_even_when_foreign_row_is_newer(
    tmp_path, owner_first,
):
    service = _service(tmp_path)
    source = tmp_path / "EBooks" / "alice" / "local" / "Book.epub"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"safe synthetic owned source")
    pid = _placement(service, source, artifact_id="sha256:legacy",
                     source_item_id="")
    valid = dict(entry_id="valid", owner="alice", title="Valid Alice title")
    foreign = dict(entry_id="foreign", owner="bob", title=PRIVATE_TITLE,
                   cover=PRIVATE_COVER)
    for row in ((valid, foreign) if owner_first else (foreign, valid)):
        _catalog(service, pid, **row)
    try:
        result = service.materialize_to_identity(pid, "bob")
        projected = [r for r in service.storage.catalog_entries()
                     if r.get("identity_id") == "bob"
                     and r.get("placement_id") == result["placement_id"]]
        assert len(projected) == 1
        assert projected[0]["title"] == "Valid Alice title"
        assert projected[0]["author"] == "Alice author"
        assert PRIVATE_TITLE not in repr(projected)
        assert PRIVATE_COVER not in repr(projected)
    finally:
        service.close()


def test_legacy_unscoped_catalogue_is_usable_only_by_default_owner(tmp_path):
    service = _service(tmp_path)
    source = tmp_path / "EBooks" / "alice" / "local" / "Book.epub"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"safe synthetic owned source")
    pid = _placement(service, source, artifact_id="sha256:legacy",
                     source_item_id="")
    _catalog(service, pid, entry_id="legacy", owner=None,
             title="Legacy title")
    try:
        result = service.materialize_to_identity(pid, "bob")
        projected = [r for r in service.storage.catalog_entries()
                     if r.get("identity_id") == "bob"
                     and r.get("placement_id") == result["placement_id"]]
        assert len(projected) == 1
        assert projected[0]["title"] == "Legacy title"
    finally:
        service.close()
