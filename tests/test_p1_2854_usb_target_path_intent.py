"""P1 #2854: USB target paths are declarative, immutable and identity-scoped.

No USB, network, acquisition, device scan or file transfer is performed.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from pathlib import Path

import pytest

from cifereca.identities import Identity
from cifereca.storage import Storage


def _db(tmp_path):
    db = Storage(tmp_path / "library.sqlite3")
    for identity in ("alice", "bob"):
        db.identity_put(Identity(identity, identity.capitalize()))
        db.device_usb_binding_declare(
            identity_id=identity, device_id=f"usb-{identity}",
            usb_fingerprint=f"uuid-{identity}-001", label=identity,
        )
    return db


def _owned_book(db, tmp_path, *, owner="alice", payload=b"USB test book"):
    path = tmp_path / owner / (sha256(payload).hexdigest() + ".epub")
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(payload)
    digest = sha256(payload).hexdigest()
    artifact_id = f"sha256:{digest}"
    db.artifact_put({
        "artifact_id": artifact_id, "sha256": digest, "size": len(payload),
        "media_format": "epub", "metadata": {"artifact_kind": "file"},
    })
    db.placement_put({
        "artifact_id": artifact_id, "path": str(path), "identity_id": owner,
        "provider_id": "local", "source_item_id": artifact_id,
        "state": "present", "metadata": {},
    })
    db.device_usb_intent_set(
        identity_id=owner, device_id=f"usb-{owner}",
        artifact_id=artifact_id, desired_state="keep",
    )
    return artifact_id, path


def _set(db, book, *, identity="alice", device="usb-alice",
         target="Books/Cifereca/book.epub"):
    return db.device_usb_target_set(
        identity_id=identity, device_id=device, artifact_id=book,
        target_relative_path=target,
    )


def test_first_target_is_exact_and_changes_no_observation_or_bytes(tmp_path):
    db = _db(tmp_path)
    book, path = _owned_book(db, tmp_path)
    before = db.device_placement_get("usb-alice", book)
    assert before["observed_state"] == "unknown"
    assert before["target_relative_path"] is None
    saved_bytes = path.read_bytes()

    placement_id = _set(db, book)
    after = db.device_placement_get("usb-alice", book)
    assert placement_id == before["device_placement_id"]
    assert after["target_relative_path"] == "Books/Cifereca/book.epub"
    assert after["desired_state"] == "keep"
    for key in ("observed_state", "last_observed_at", "metadata",
                "device_id", "artifact_id", "created_at"):
        assert after[key] == before[key]
    assert after["metadata"] == {"intent_identity_id": "alice"}
    assert path.read_bytes() == saved_bytes
    assert db.device_placements(device_id="usb-bob") == []
    assert _set(db, book) == placement_id
    assert db.device_placement_get("usb-alice", book)["metadata"] == before["metadata"]


def test_once_pinned_target_cannot_silently_move_or_orphan_receipt(tmp_path):
    db = _db(tmp_path)
    book, _ = _owned_book(db, tmp_path)
    _set(db, book)
    old = db.device_placement_get("usb-alice", book)
    db.device_placement_put({
        **old, "observed_state": "present",
        "last_observed_at": 1720000000,
        "metadata": {
            "intent_identity_id": "alice",
            "managed_receipt": {"sha256": "previous-write"},
        },
    })
    before = deepcopy(db.device_placements())
    with pytest.raises(ValueError, match="migration"):
        _set(db, book, target="Books/Cifereca/renamed.epub")
    assert db.device_placements() == before
    assert _set(db, book) == old["device_placement_id"]
    assert db.device_placements() == before


@pytest.mark.parametrize("observed,when,metadata", [
    ("present", 1720000000, {"intent_identity_id": "alice"}),
    ("missing", 1720000000, {"intent_identity_id": "alice"}),
    ("unknown", 1720000000, {"intent_identity_id": "alice"}),
    ("unknown", None, {"intent_identity_id": "alice", "receipt": "old"}),
])
def test_cannot_assign_target_to_a_historic_unmapped_observation(
    tmp_path, observed, when, metadata,
):
    db = _db(tmp_path)
    book, _ = _owned_book(db, tmp_path)
    row = db.device_placement_get("usb-alice", book)
    db.device_placement_put({
        **row, "observed_state": observed,
        "last_observed_at": when,
        "metadata": metadata,
    })
    before = deepcopy(db.device_placements())
    with pytest.raises(ValueError, match="prior observation"):
        _set(db, book)
    assert db.device_placements() == before


def test_two_books_cannot_reserve_casefold_colliding_targets(tmp_path):
    db = _db(tmp_path)
    first, _ = _owned_book(db, tmp_path, payload=b"first")
    second, _ = _owned_book(db, tmp_path, payload=b"second")
    _set(db, first, target="Books/Cifereca/Novel.epub")
    before = deepcopy(db.device_placements())
    with pytest.raises(ValueError, match="already reserved"):
        _set(db, second, target="Books/Cifereca/novel.EPUB")
    assert db.device_placements() == before
    assert _set(db, second, target="Books/Cifereca/second.epub")
    assert len(db.device_placements(device_id="usb-alice")) == 2


def test_same_target_can_be_reserved_on_different_devices(tmp_path):
    db = _db(tmp_path)
    book, _ = _owned_book(db, tmp_path)
    db.device_usb_binding_declare(
        identity_id="alice", device_id="usb-alice-second",
        usb_fingerprint="uuid-alice-002", label="Secondary",
    )
    db.device_usb_intent_set(
        identity_id="alice", device_id="usb-alice-second",
        artifact_id=book, desired_state="keep",
    )
    _set(db, book)
    second = _set(db, book, device="usb-alice-second")
    assert db.device_placement_get("usb-alice-second", book)["device_placement_id"] == second
    assert db.device_placement_get("usb-alice", book)["target_relative_path"] == (
        "Books/Cifereca/book.epub"
    )


def test_foreign_identity_and_missing_intent_do_not_create_target(tmp_path):
    db = _db(tmp_path)
    book, _ = _owned_book(db, tmp_path)
    before = deepcopy(db.device_placements())
    with pytest.raises(ValueError, match="bound identity"):
        _set(db, book, identity="bob")
    with pytest.raises(ValueError, match="existing intention"):
        _set(db, book, device="usb-bob", identity="bob")
    assert db.device_placements() == before


def test_rebound_device_cannot_transfer_existing_target_to_new_profile(tmp_path):
    db = _db(tmp_path)
    book, _ = _owned_book(db, tmp_path)
    db.device_binding_put({
        "device_id": "usb-alice", "identity_id": "bob",
        "target_directory": "Books/Cifereca",
        "metadata": {"usb_fingerprint": "uuid-bob-002"},
    })
    before = deepcopy(db.device_placements())
    with pytest.raises(ValueError, match="bound identity"):
        _set(db, book)
    with pytest.raises(ValueError, match="unproven"):
        _set(db, book, identity="bob")
    assert db.device_placements() == before


def test_unproven_legacy_intent_is_never_claimed_by_target_writer(tmp_path):
    db = _db(tmp_path)
    db.device_placement_put({
        "device_id": "usb-alice", "artifact_id": "old-book",
        "desired_state": "keep", "observed_state": "unknown",
        "metadata": {},
    })
    before = deepcopy(db.device_placements())
    with pytest.raises(ValueError, match="unproven"):
        _set(db, "old-book")
    assert db.device_placements() == before


def test_remove_intent_does_not_gain_an_arbitrary_target(tmp_path):
    db = _db(tmp_path)
    book, _ = _owned_book(db, tmp_path)
    db.device_usb_intent_set(
        identity_id="alice", device_id="usb-alice",
        artifact_id=book, desired_state="remove",
    )
    before = deepcopy(db.device_placements())
    with pytest.raises(ValueError, match="keep intention"):
        _set(db, book)
    assert db.device_placements() == before


@pytest.mark.parametrize("bad", [
    None, 12, True, "", "book.epub", "../private.epub",
    "/Books/Cifereca/book.epub", "Books/../Cifereca/book.epub",
    "Books/Cifereca/../../book.epub", "Books/Cifereca/",
    "Books/Cifereca/book?.epub", "Books/Cifereca/con.txt",
    "Books/Cifereca/PRN", "Books/Cifereca/COM1.txt",
    "Books/Cifereca/book.epub.", "Books/Cifereca/book.epub ",
    "Books/Cifereca/sub\\outside.epub",
    "Books/Cifereca//missing.epub", "Books/Cifereca/file:colon.epub",
    "Books/Cifereca/\u0000nul.epub", "Books/Cifereca/\nnew.epub",
    "Books/Foreign/not-managed.epub",
])
def test_invalid_usb_filenames_are_inert(tmp_path, bad):
    db = _db(tmp_path)
    book, _ = _owned_book(db, tmp_path)
    before = deepcopy(db.device_placements())
    with pytest.raises(ValueError):
        _set(db, book, target=bad)
    assert db.device_placements() == before


def test_existing_intent_works_with_disabled_device_without_implying_copy(tmp_path):
    db = _db(tmp_path)
    book, _ = _owned_book(db, tmp_path)
    assert db.device_binding_get("usb-alice")["enabled"] is False
    _set(db, book)
    assert db.device_binding_get("usb-alice")["enabled"] is False
    row = db.device_placement_get("usb-alice", book)
    assert row["observed_state"] == "unknown"
    assert row["last_observed_at"] is None


def test_pinned_target_persists_without_creating_usb_observations(tmp_path):
    db = _db(tmp_path)
    book, _ = _owned_book(db, tmp_path)
    _set(db, book)
    restarted = Storage(tmp_path / "library.sqlite3")
    row = restarted.device_placement_get("usb-alice", book)
    assert row["target_relative_path"] == "Books/Cifereca/book.epub"
    assert row["observed_state"] == "unknown"
    assert row["last_observed_at"] is None
