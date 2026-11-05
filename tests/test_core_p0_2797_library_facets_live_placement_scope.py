"""P0 #2797/#2847: a Library card's second Placement must be openable.

The catalogue may have one valid local copy while SQLite still marks a second
lost or foreign path 'present'. Never expose the second filename/target.
Real LibraryService + SQLite/FS; no provider requests or mutations.
"""
from __future__ import annotations

import hashlib

import pytest

from tests.test_core_library_placement_identity_scope import _service, _placement

BYTES = b"same epub contents"
SHA = hashlib.sha256(BYTES).hexdigest()
ARTIFACT = "sha256:" + SHA


def _book(store):
    store.artifact_put({
        "artifact_id": ARTIFACT,
        "sha256": SHA,
        "size": len(BYTES),
        "media_format": "epub",
        "metadata": {},
    })


def _facets(service):
    cards = [
        row for row in service.library_entries(identity_id="alice", scan=False)
        if row.get("artifact_id") == ARTIFACT and row.get("local") is True
    ]
    assert len(cards) == 1
    return cards[0]


@pytest.mark.parametrize("change", ("missing", "dangling_link", "foreign_link", "directory"))
def test_secondary_stale_placement_hidden_without_losing_valid_local_card(
    tmp_path, change,
):
    service = _service(tmp_path)
    try:
        _book(service.storage)
        keeper = _placement(service, tmp_path, owner="alice",
                            filename="Keeper.epub", artifact_id=ARTIFACT,
                            item_id="owned-1")
        old = _placement(service, tmp_path, owner="alice",
                         filename="Old-copy.epub", artifact_id=ARTIFACT,
                         item_id="owned-2")
        before = service.storage.placements()
        initial = _facets(service)
        assert {row["placement_id"] for row in initial["placements"]} == {
            keeper, old,
        }
        path = tmp_path / "alice" / "Old-copy.epub"
        path.unlink()
        if change == "dangling_link":
            path.symlink_to(path.parent / "vanished.epub")
        elif change == "foreign_link":
            foreign = tmp_path / "library" / "bob" / "PRIVATE-BOOK.epub"
            foreign.parent.mkdir(parents=True, exist_ok=True)
            foreign.write_bytes(BYTES)
            path.symlink_to(foreign)
        elif change == "directory":
            path.mkdir()

        card = _facets(service)
        assert [row["placement_id"] for row in card["placements"]] == [keeper]
        assert card["placement_count"] == 1
        assert "Old-copy.epub" not in repr(card["placements"])
        assert service.storage.placements() == before

        if path.is_symlink():
            path.unlink()
        elif path.is_dir():
            path.rmdir()
        path.write_bytes(BYTES)
        restored = _facets(service)
        assert {row["placement_id"] for row in restored["placements"]} == {
            keeper, old,
        }
        assert service.storage.placements() == before
    finally:
        service.close()


def test_corrupt_foreign_owner_does_not_expose_other_profile_filename(
    tmp_path,
):
    service = _service(tmp_path)
    try:
        _book(service.storage)
        keeper = _placement(service, tmp_path, owner="alice",
                            filename="Kept.epub", artifact_id=ARTIFACT,
                            item_id="owned-1")
        foreign = tmp_path / "library" / "bob" / "BOB_PRIVATE_FILENAME.epub"
        foreign.parent.mkdir(parents=True, exist_ok=True)
        foreign.write_bytes(BYTES)
        foreign_id = service.storage.placement_put({
            "artifact_id": ARTIFACT,
            "path": str(foreign),
            "identity_id": "bob",
            "provider_id": "local",
            "source_item_id": "private-source",
            "state": "present",
        })
        # Simulate a corrupt/imported DB owner claim; the physical root
        # still belongs to Bob, and no title/SHA equality can move it.
        with service.storage._lock, service.storage._connect() as db:
            db.execute(
                "UPDATE placements SET identity_id='alice' WHERE placement_id=?",
                (foreign_id,),
            )
        before = service.storage.placements()
        card = _facets(service)
        assert [row["placement_id"] for row in card["placements"]] == [keeper]
        assert "BOB_PRIVATE_FILENAME" not in repr(card)
        assert service.storage.placements() == before
        assert foreign.read_bytes() == BYTES
    finally:
        service.close()
