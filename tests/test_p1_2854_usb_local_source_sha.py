"""P1 #2854: local file bytes can be verified, never granted as a USB right."""
from __future__ import annotations

from hashlib import sha256
import os
from pathlib import Path

import pytest

from cifereca.core.device_usb_local_source import attest_local_usb_source
from cifereca.core.device_usb_preview import (
    UsbDeviceScan, preview_usb_delta,
)


BYTES = b"test epub bytes for an owned local fixture"
DIGEST = sha256(BYTES).hexdigest()


def evidence(tmp_path, *, owner="alice", selected="alice", content=BYTES):
    directory = tmp_path / owner
    directory.mkdir(exist_ok=True)
    path = directory / "book.epub"
    path.write_bytes(content)
    artifact = {
        "artifact_id": "sha256:" + DIGEST, "sha256": DIGEST,
        "size": len(BYTES), "kind": "file",
    }
    placement = {
        "artifact_id": artifact["artifact_id"], "identity_id": owner,
        "state": "present", "path": str(path),
    }
    result = attest_local_usb_source(
        identity_id=selected, identity_root=directory,
        artifact=artifact, placement=placement,
    )
    return result, artifact, placement, path


def test_exact_owned_bytes_produce_no_transfer_permission(tmp_path):
    source, artifact, placement, path = evidence(tmp_path)
    assert source is not None
    assert source.identity_id == "alice"
    assert source.sha256 == DIGEST
    assert source.verified is True
    assert not hasattr(source, "path")
    assert path.read_bytes() == BYTES


def test_locally_verified_sha_is_only_an_advisory_preview_input(tmp_path):
    local, artifact, placement, _ = evidence(tmp_path)
    device = {
        "device_id": "reader-a", "identity_id": "alice",
        "enabled": True, "metadata": {"usb_fingerprint": "uuid-001"},
    }
    intention = {
        "device_id": "reader-a", "artifact_id": artifact["artifact_id"],
        "desired_state": "keep", "observed_state": "unknown",
        # The source SHA is verified, but the USB destination must be explicit.
        "target_relative_path": "Books/Cifereca/book.epub",
        "metadata": {"intent_identity_id": "alice"},
    }
    # The USB observation here is synthetic, to test composition of pure
    # contracts. No actual mount is ever claimed or touched.
    scan = UsbDeviceScan(
        identity_id="alice", device_id="reader-a",
        fingerprint="uuid-001", observed_at=1800000000,
        complete=True, files={},
    )
    row, = preview_usb_delta(
        identity_id="alice", binding=device,
        placements=[intention], local={artifact["artifact_id"]: local},
        scan=scan, now=1800000000,
    )
    assert (row.action, row.observed) == ("upload_candidate", "missing")
    assert row.executable is False


def test_no_cross_profile_source_evidence_even_when_sha_is_shared(tmp_path):
    source, artifact, placement, _ = evidence(
        tmp_path, owner="alice", selected="bob",
    )
    assert source is None
    root = tmp_path / "bob"
    root.mkdir()
    spoof = attest_local_usb_source(
        identity_id="bob", identity_root=root,
        artifact=artifact, placement=placement,
    )
    assert spoof is None


def test_same_size_tampered_bytes_are_rejected(tmp_path):
    source, artifact, placement, path = evidence(
        tmp_path, content=b"X" * len(BYTES),
    )
    assert source is None
    assert path.stat().st_size == artifact["size"]
    assert sha256(path.read_bytes()).hexdigest() != DIGEST


@pytest.mark.parametrize("change", [
    {"sha256": "0" * 64},
    {"sha256": DIGEST.upper()},
    {"size": len(BYTES) + 1},
    {"kind": "bundle"},
    {"kind": "zip"},
    {"kind": True},
    {"artifact_id": "another-id"},
])
def test_artifact_metadata_forgery_is_not_attestation(tmp_path, change):
    _, artifact, placement, _ = evidence(tmp_path)
    artifact.update(change)
    assert attest_local_usb_source(
        identity_id="alice", identity_root=tmp_path / "alice",
        artifact=artifact, placement=placement,
    ) is None


@pytest.mark.parametrize("change", [
    {"state": "missing"},
    {"state": "dedup_cleanup_pending"},
    {"identity_id": "bob"},
    {"artifact_id": "different"},
    {"path": "/tmp/not-in-selected-profile.epub"},
])
def test_unowned_missing_or_wrongly_scoped_placement_is_rejected(tmp_path, change):
    _, artifact, placement, _ = evidence(tmp_path)
    placement.update(change)
    assert attest_local_usb_source(
        identity_id="alice", identity_root=tmp_path / "alice",
        artifact=artifact, placement=placement,
    ) is None


def test_leaf_symlink_to_other_profile_is_rejected(tmp_path):
    _, artifact, placement, path = evidence(tmp_path)
    bob = tmp_path / "bob.epub"
    bob.write_bytes(BYTES)
    path.unlink()
    path.symlink_to(bob)
    assert attest_local_usb_source(
        identity_id="alice", identity_root=tmp_path / "alice",
        artifact=artifact, placement=placement,
    ) is None


def test_symlinked_parent_is_not_traversed(tmp_path):
    _, artifact, placement, path = evidence(tmp_path)
    external = tmp_path / "external"
    external.mkdir()
    data = external / "book.epub"
    data.write_bytes(BYTES)
    path.unlink()
    alias = tmp_path / "alice" / "alias"
    alias.symlink_to(external, target_is_directory=True)
    placement["path"] = str(alias / "book.epub")
    assert attest_local_usb_source(
        identity_id="alice", identity_root=tmp_path / "alice",
        artifact=artifact, placement=placement,
    ) is None


def test_lexical_escape_and_root_symlink_are_rejected(tmp_path):
    _, artifact, placement, _ = evidence(tmp_path)
    placement["path"] = str(tmp_path / "alice" / ".." / "alice" / "book.epub")
    assert attest_local_usb_source(
        identity_id="alice", identity_root=tmp_path / "alice",
        artifact=artifact, placement=placement,
    ) is None
    alias = tmp_path / "alice-alias"
    alias.symlink_to(tmp_path / "alice", target_is_directory=True)
    placement["path"] = str(alias / "book.epub")
    assert attest_local_usb_source(
        identity_id="alice", identity_root=alias,
        artifact=artifact, placement=placement,
    ) is None


def test_nonexistent_file_and_invalid_types_are_fail_closed(tmp_path):
    _, artifact, placement, path = evidence(tmp_path)
    path.unlink()
    assert attest_local_usb_source(
        identity_id="alice", identity_root=tmp_path / "alice",
        artifact=artifact, placement=placement,
    ) is None
    assert attest_local_usb_source(
        identity_id="alice", identity_root=str(tmp_path / "alice"),
        artifact=artifact, placement=placement,
    ) is None
    assert attest_local_usb_source(
        identity_id="alice", identity_root=tmp_path / "alice",
        artifact=artifact, placement=placement, max_bytes=True,
    ) is None


def test_source_is_reread_not_trusted_after_local_mutation(tmp_path):
    proof, artifact, placement, path = evidence(tmp_path)
    assert proof is not None
    path.write_bytes(b"Y" * len(BYTES))
    new_proof = attest_local_usb_source(
        identity_id="alice", identity_root=tmp_path / "alice",
        artifact=artifact, placement=placement,
    )
    assert new_proof is None


def test_source_uses_registered_legacy_artifact_kind_metadata(tmp_path):
    _, artifact, placement, _ = evidence(tmp_path)
    artifact.pop("kind")
    artifact["metadata"] = {"artifact_kind": "file"}
    assert attest_local_usb_source(
        identity_id="alice", identity_root=tmp_path / "alice",
        artifact=artifact, placement=placement,
    ).verified is True
