"""P1 #2854: physical local byte snapshot is SHA- and inode-bound, inert."""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import pytest

from cifereca.core.device_usb_content_snapshot import (
    MAX_SNAPSHOT_BYTES, snapshot_local_usb_content,
)
from cifereca.core.device_usb_preview import UsbLocalArtifact


BYTES = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF\n"
SHA = sha256(BYTES).hexdigest()


def scoped(tmp_path: Path):
    root = tmp_path / "alice"
    root.mkdir()
    file = root / "book.pdf"
    file.write_bytes(BYTES)
    artifact = {
        "artifact_id": "sha256:" + SHA, "sha256": SHA,
        "size": len(BYTES), "kind": "file", "media_format": "pdf",
    }
    placement = {
        "artifact_id": artifact["artifact_id"],
        "identity_id": "alice", "state": "present", "path": str(file),
    }
    return root, file, artifact, placement, UsbLocalArtifact("alice", SHA, True)


def read(root, artifact, placement, source, **changes):
    data = dict(
        identity_id="alice", identity_root=root, artifact=artifact,
        placement=placement, local=source,
    )
    data.update(changes)
    return snapshot_local_usb_content(**data)


def test_exact_owned_file_is_read_without_side_effect(tmp_path):
    root, file, artifact, placement, local = scoped(tmp_path)
    before = file.stat()
    assert read(root, artifact, placement, local) == BYTES
    after = file.stat()
    assert (before.st_size, before.st_mtime_ns) == (
        after.st_size, after.st_mtime_ns,
    )


@pytest.mark.parametrize("change", ["tamper", "truncate", "symlink", "escape"])
def test_stale_hash_symlink_or_outside_root_is_rejected(tmp_path, change):
    root, file, artifact, placement, local = scoped(tmp_path)
    if change == "tamper":
        file.write_bytes(b"X" * len(BYTES))
    elif change == "truncate":
        file.write_bytes(b"%PDF-")
    elif change == "symlink":
        original = root / "outside.pdf"
        original.write_bytes(BYTES)
        file.unlink()
        file.symlink_to(original)
    else:
        other = tmp_path / "foreign.pdf"
        other.write_bytes(BYTES)
        placement = {**placement, "path": str(other)}
    assert read(root, artifact, placement, local) is None


@pytest.mark.parametrize("kwargs", [
    {"identity_id": "bob"},
    {"local": UsbLocalArtifact("bob", SHA, True)},
    {"local": UsbLocalArtifact("alice", SHA, False)},
    {"local": UsbLocalArtifact("alice", "0" * 64, True)},
    {"max_bytes": 1},
    {"max_bytes": MAX_SNAPSHOT_BYTES + 1},
    {"max_bytes": True},
])
def test_invalid_scope_or_budget_refuses_bytes(tmp_path, kwargs):
    root, file, artifact, placement, local = scoped(tmp_path)
    assert read(root, artifact, placement, local, **kwargs) is None


def test_relinked_parent_directory_is_never_followed(tmp_path):
    root, file, artifact, placement, local = scoped(tmp_path)
    holder = tmp_path / "hidden"
    holder.mkdir()
    inside = root / "nested"
    inside.mkdir()
    nested = inside / "book.pdf"
    nested.write_bytes(BYTES)
    placement = {**placement, "path": str(nested)}
    inside.rename(holder / "old")
    inside.symlink_to(holder / "old", target_is_directory=True)
    assert read(root, artifact, placement, local) is None


def test_cross_artifact_or_misreported_size_is_rejected(tmp_path):
    root, file, artifact, placement, local = scoped(tmp_path)
    assert read(root, {**artifact, "size": len(BYTES) + 1}, placement, local) is None
    assert read(root, artifact, {**placement, "artifact_id": "other"}, local) is None
    assert read(root, {**artifact, "sha256": "0" * 64}, placement, local) is None
