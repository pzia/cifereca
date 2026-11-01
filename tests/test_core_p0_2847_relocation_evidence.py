"""#2847: inspect a target's current bytes without relinking provenance or files."""
from __future__ import annotations

import hashlib

import pytest

from cifereca.core.missing_placement_relocation_preview import (
    preview_missing_placement_relocation,
)
from cifereca.storage import Storage


@pytest.fixture
def rig(tmp_path):
    storage = Storage(tmp_path / "db" / "local.sqlite3")
    root = tmp_path / "library"
    directory = root / "alice" / "kobo"
    directory.mkdir(parents=True)
    other = root / "bob" / "kobo"
    other.mkdir(parents=True)
    for identity in ("alice", "bob"):
        storage.identity_put({
            "identity_id": identity, "label": identity.title(),
            "directory_name": identity,
        })
    old = directory / "was-here.epub"
    new = directory / "now-here.epub"
    data = b"synthetic local bytes for same artifact"
    new.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    artifact_id = "sha256:" + digest
    storage.artifact_put({
        "artifact_id": artifact_id, "sha256": digest,
        "size": len(data), "media_format": "epub",
    })
    placement = storage.placement_put({
        "path": str(old), "identity_id": "alice",
        "provider_id": "kobo", "source_item_id": "book-a",
        "artifact_id": artifact_id, "state": "missing",
        "metadata": {},
    })
    yield storage, root, old, new, placement, artifact_id, other, data
    storage.close()


def preview(rig, *, target=None, identity="alice", root=None):
    store, library, _old, new, placement, *_ = rig
    return preview_missing_placement_relocation(
        store, library_root=root or library,
        identity_id=identity, placement_id=placement,
        candidate_path=target or new,
    )


def test_exact_bytes_review_is_read_only(rig):
    store, root, old, new, placement, artifact, other, data = rig
    previous = store.placement_get(placement)
    result = preview(rig)
    assert result["status"] == "review_required"
    assert result["reason"] == "exact_bytes_candidate"
    assert result["candidate_bytes_verified"] is True
    assert result["requires_confirmation"] is True
    assert len(result["plan_token"]) == 64
    assert result["catalogue_rows"] == 0
    assert result["backup_rows"] == 0
    assert str(new) not in str(result)
    assert store.placement_get(placement) == previous
    assert new.read_bytes() == data
    assert not old.exists()


def test_linked_catalogue_requests_explicit_reference_migration(rig):
    store, root, old, new, placement, artifact, other, data = rig
    store.catalog_entry_put({
        "catalog_entry_id": "entry-1", "placement_id": placement,
        "artifact_id": artifact, "identity_id": "alice",
        "provider_id": "kobo", "item_id": "book-a",
        "title": "Synthetic book", "local_state": "missing",
        "metadata": {"path": str(old)},
    })
    result = preview(rig)
    assert result["status"] == "review_required"
    assert result["reason"] == "references_require_migration"
    assert result["catalogue_rows"] == 1
    assert store.catalog_entry_get("entry-1")["local_state"] == "missing"
    assert store.placement_get(placement)["path"] == str(old)


def test_old_path_returns_blocks_preview(rig):
    store, root, old, new, placement, artifact, other, data = rig
    old.write_bytes(data)
    assert preview(rig)["reason"] == "old_path_not_verified_absent"


@pytest.mark.parametrize("kind", ["different_bytes", "symlink", "foreign_profile"])
def test_no_attestation_from_tampered_or_cross_identity_file(rig, kind):
    store, root, old, new, placement, artifact, other, data = rig
    if kind == "different_bytes":
        new.write_bytes(data + b"tampered")
        target = new
    elif kind == "symlink":
        new.unlink()
        real = new.with_name("actual.epub")
        real.write_bytes(data)
        new.symlink_to(real)
        target = new
    else:
        target = other / "same.epub"
        target.write_bytes(data)
    assert preview(rig, target=target)["reason"] == "candidate_bytes_unverified"
    assert store.placement_get(placement)["state"] == "missing"


def test_target_placement_collision_blocks_even_same_sha(rig):
    store, root, old, new, placement, artifact, other, data = rig
    store.placement_put({
        "path": str(new), "identity_id": "alice",
        "provider_id": "kobo", "source_item_id": "book-a",
        "artifact_id": artifact, "state": "present",
    })
    assert preview(rig)["reason"] == "target_placement_occupied"
    assert store.placement_get(placement)["state"] == "missing"


def test_legacy_backup_claim_on_target_blocks(rig):
    store, root, old, new, placement, artifact, other, data = rig
    with store._lock, store._connect() as db:
        db.execute(
            "INSERT INTO backup_items(provider_id,credential_id,item_id,"
            "title,status,updated_at,local_path) VALUES(?,?,?,?,?,?,?)",
            ("other", "login", "different-book", "Protected", "complete",
             1, str(new)),
        )
    assert preview(rig)["reason"] == "target_backup_occupied"


def test_old_path_checkpoint_kept_and_review_required(rig):
    store, root, old, new, placement, artifact, other, data = rig
    store.metadata_set("synthetic.acquisition.pending", str(old))
    result = preview(rig)
    assert result["reason"] == "references_require_migration"
    assert store.metadata_get("synthetic.acquisition.pending") == str(old)


def test_ambiguous_identity_directories_fail_closed(rig):
    store, root, old, new, placement, artifact, other, data = rig
    store.identity_put({
        "identity_id": "bob", "directory_name": "alice", "label": "Bob",
    })
    assert preview(rig)["reason"] == "ambiguous_identity_directory"


def test_root_and_ownership_are_not_inferred_from_checksum(rig, tmp_path):
    store, root, old, new, placement, artifact, other, data = rig
    assert preview(rig, identity="bob")["reason"] == "identity_mismatch"
    assert preview(rig, root=tmp_path / "not-mounted")["reason"] == "root_unavailable"
    assert store.placement_get(placement)["state"] == "missing"
