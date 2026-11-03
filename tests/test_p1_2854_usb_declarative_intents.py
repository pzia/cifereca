"""P1 #2854: declarative USB intent must be atomic, scoped and non-destructive.

No USB scan, network, acquisition, filesystem deletion or transfer is called.
SQLite changes represent *wishes*, never evidence of device presence.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256

import pytest

from cifereca.identities import Identity
from cifereca.storage import Storage
from cifereca.core.device_usb_preview import (
    UsbLocalArtifact, preview_usb_delta,
)


def _library(tmp_path):
    db = Storage(tmp_path / "library.sqlite3")
    for owner in ("alice", "bob"):
        db.identity_put(Identity(owner, owner.capitalize()))
        db.device_binding_put({
            "device_id": f"reader-{owner}", "identity_id": owner,
            "label": f"USB {owner}",
            "enabled": True,
            "metadata": {"usb_fingerprint": f"uuid-{owner}"},
        })
    raw = b"local USB declarative test book"
    digest = sha256(raw).hexdigest()
    artifact_id = f"sha256:{digest}"
    db.artifact_put({
        "artifact_id": artifact_id, "sha256": digest, "size": len(raw),
        "media_format": "epub", "metadata": {"artifact_kind": "file"},
    })
    local_file = tmp_path / "alice" / "book.epub"
    local_file.parent.mkdir()
    local_file.write_bytes(raw)
    db.placement_put({
        "artifact_id": artifact_id, "path": str(local_file),
        "identity_id": "alice", "provider_id": "local",
        "source_item_id": "local-book", "state": "present", "metadata": {},
    })
    return db, artifact_id, digest


def test_new_intent_is_only_desired_keep_with_unknown_observation(tmp_path):
    db, artifact, digest = _library(tmp_path)
    before_local = deepcopy(db.placements())
    first = db.device_usb_intent_set(
        identity_id="alice", device_id="reader-alice",
        artifact_id=artifact, desired_state="keep",
    )
    row = db.device_placement_get("reader-alice", artifact)
    assert first == row["device_placement_id"]
    assert row["desired_state"] == "keep"
    assert row["observed_state"] == "unknown"
    assert row["last_observed_at"] is None
    assert row["target_relative_path"] is None
    assert row["metadata"] == {"intent_identity_id": "alice"}
    assert db.placements() == before_local
    assert db.device_placements(device_id="reader-bob") == []
    # A stored wish never claims success or local ownership for another profile.
    shown, = preview_usb_delta(
        identity_id="alice", binding=db.device_binding_get("reader-alice"),
        placements=[row],
        local={artifact: UsbLocalArtifact("alice", digest, True)},
        now=1800000000,
    )
    assert (shown.action, shown.observed, shown.last_known) == (
        "verify", "unknown", "unknown",
    )
    assert not shown.executable


def test_intent_change_preserves_receipt_observation_and_path(tmp_path):
    db, artifact, _digest = _library(tmp_path)
    id_ = db.device_usb_intent_set(
        identity_id="alice", device_id="reader-alice",
        artifact_id=artifact, desired_state="keep",
    )
    # Synthetic observed state belongs to the future USB observer, not to the
    # declarative setter. In particular, its opaque receipt must survive.
    original = db.device_placement_get("reader-alice", artifact)
    db.device_placement_put({
        **original, "observed_state": "present",
        "last_observed_at": 1720000000,
        "target_relative_path": "Books/Cifereca/book.epub",
        "metadata": {
            "intent_identity_id": "alice",
            "managed_receipt": {"sha256": "owned-sha", "owner": "alice"},
            "opaque_plugin_v2": {"source": "usb"},
        },
    })
    before = db.device_placement_get("reader-alice", artifact)
    result = db.device_usb_intent_set(
        identity_id="alice", device_id="reader-alice",
        artifact_id=artifact, desired_state="remove",
    )
    after = db.device_placement_get("reader-alice", artifact)
    assert result == id_
    for key in ("device_placement_id", "observed_state", "last_observed_at",
                "target_relative_path", "metadata", "created_at"):
        assert after[key] == before[key]
    assert after["desired_state"] == "remove"
    again = db.device_usb_intent_set(
        identity_id="alice", device_id="reader-alice",
        artifact_id=artifact, desired_state="keep",
    )
    assert again == id_
    assert db.device_placement_get("reader-alice", artifact)["metadata"] == before["metadata"]
    assert len(db.device_placements(device_id="reader-alice")) == 1


def test_unknown_removed_book_must_not_create_an_unmanaged_delete_intent(tmp_path):
    db, artifact, _ = _library(tmp_path)
    before = deepcopy(db.device_placements())
    with pytest.raises(ValueError, match="unmanaged"):
        db.device_usb_intent_set(
            identity_id="alice", device_id="reader-alice",
            artifact_id=artifact, desired_state="remove",
        )
    assert db.device_placements() == before


def test_only_exact_local_identity_placement_can_create_keep_intent(tmp_path):
    db, artifact, _ = _library(tmp_path)
    before = deepcopy(db.device_placements())
    # The same SHA is in global artifacts, but Bob has no local placement.
    with pytest.raises(ValueError, match="local identity"):
        db.device_usb_intent_set(
            identity_id="bob", device_id="reader-bob",
            artifact_id=artifact, desired_state="keep",
        )
    assert db.device_placements() == before
    # Another profile may explicitly declare after its *own* local placement
    # exists; this never copies Alice's file or creates a cross-profile right.
    second = tmp_path / "bob" / "second.epub"
    second.parent.mkdir()
    second.write_bytes(b"local USB declarative test book")
    db.placement_put({
        "artifact_id": artifact, "path": str(second),
        "identity_id": "bob", "provider_id": "local",
        "source_item_id": "bob-owned-copy", "state": "present",
        "metadata": {},
    })
    db.device_usb_intent_set(
        identity_id="bob", device_id="reader-bob",
        artifact_id=artifact, desired_state="keep",
    )
    bob = db.device_placement_get("reader-bob", artifact)
    assert bob["metadata"]["intent_identity_id"] == "bob"
    assert db.device_placement_get("reader-alice", artifact) is None


def test_reassigned_device_cannot_inherit_an_old_intent(tmp_path):
    db, artifact, _ = _library(tmp_path)
    db.device_usb_intent_set(
        identity_id="alice", device_id="reader-alice",
        artifact_id=artifact, desired_state="keep",
    )
    before = deepcopy(db.device_placements())
    db.device_binding_put({
        "device_id": "reader-alice", "identity_id": "bob",
        "metadata": {"usb_fingerprint": "uuid-bob"},
    })
    with pytest.raises(ValueError, match="unproven"):
        db.device_usb_intent_set(
            identity_id="bob", device_id="reader-alice",
            artifact_id=artifact, desired_state="remove",
        )
    with pytest.raises(ValueError, match="bound identity"):
        db.device_usb_intent_set(
            identity_id="alice", device_id="reader-alice",
            artifact_id=artifact, desired_state="remove",
        )
    assert db.device_placements() == before


def test_legacy_placement_without_origin_proof_cannot_be_claimed(tmp_path):
    db, artifact, _ = _library(tmp_path)
    db.device_placement_put({
        "device_id": "reader-alice", "artifact_id": artifact,
        "desired_state": "keep", "observed_state": "present",
        "last_observed_at": 1720000000, "metadata": {},
    })
    before = deepcopy(db.device_placements())
    for desired in ("keep", "remove"):
        with pytest.raises(ValueError, match="unproven"):
            db.device_usb_intent_set(
                identity_id="alice", device_id="reader-alice",
                artifact_id=artifact, desired_state=desired,
            )
    assert db.device_placements() == before


@pytest.mark.parametrize("invalid", [
    None, True, False, 1, 0, "KEEP", "remove ", "", {}, [],
])
def test_untyped_or_unrecognized_desired_state_never_writes(tmp_path, invalid):
    db, artifact, _ = _library(tmp_path)
    before = deepcopy(db.device_placements())
    with pytest.raises(ValueError, match="must be keep or remove"):
        db.device_usb_intent_set(
            identity_id="alice", device_id="reader-alice",
            artifact_id=artifact, desired_state=invalid,
        )
    assert db.device_placements() == before


@pytest.mark.parametrize("key,invalid", [
    ("identity_id", "bob"), ("device_id", "reader-bob"),
    ("identity_id", None), ("device_id", 7), ("artifact_id", ""),
])
def test_foreign_untyped_or_missing_binding_never_writes(tmp_path, key, invalid):
    db, artifact, _ = _library(tmp_path)
    values = dict(
        identity_id="alice", device_id="reader-alice",
        artifact_id=artifact, desired_state="keep",
    )
    values[key] = invalid
    before = deepcopy(db.device_placements())
    with pytest.raises(ValueError):
        db.device_usb_intent_set(**values)
    assert db.device_placements() == before


def test_declarative_intent_survives_storage_reopen_without_usb_io(tmp_path):
    db, artifact, _ = _library(tmp_path)
    identity = db.device_usb_intent_set(
        identity_id="alice", device_id="reader-alice",
        artifact_id=artifact, desired_state="keep",
    )
    restored = Storage(tmp_path / "library.sqlite3")
    row = restored.device_placement_get("reader-alice", artifact)
    assert row["device_placement_id"] == identity
    assert row["desired_state"] == "keep"
    assert row["observed_state"] == "unknown"
    assert row["metadata"] == {"intent_identity_id": "alice"}
