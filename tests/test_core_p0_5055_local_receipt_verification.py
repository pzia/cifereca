"""P0 #5055: local receipt is a passive same-source physical proof, not a right."""
from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.core import local_receipt_verification as evidence


@pytest.fixture()
def rig(tmp_path, monkeypatch):
    root = tmp_path / "alice" / "fictional-provider"
    leaf = root / "loan-1"
    leaf.mkdir(parents=True)
    path = leaf / "book.epub"
    path.write_bytes(b"fake but freshly inspected bytes")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    artifact_id = "sha256:" + digest
    artifact = {"sha256": digest, "size": len(path.read_bytes())}
    placement = {
        "state": "present", "identity_id": "alice",
        "provider_id": "fictional-provider", "artifact_id": artifact_id,
    }
    row = {
        "identity_id": "alice", "provider_id": "manual",
        "credential_id": "manual", "artifact_id": artifact_id,
    }
    storage = NS(
        artifact_get=lambda _id: artifact,
        placement_for_path=lambda _path: placement,
    )
    service = NS(
        storage=storage,
        library_identity_provider_dir=lambda identity, provider:
            tmp_path / identity / provider,
    )
    monkeypatch.setattr(evidence, "inspect_artifact",
                        lambda _path: NS(valid=True, media_format="epub"))
    return service, path, row, artifact, placement, root


def verify(service, path, row):
    return evidence.local_receipt_file_verified(
        service, identity_id="alice", placement_provider_id="fictional-provider",
        acquisition_provider_id="manual", acquisition_credential_id="manual",
        row=row, path=path,
    )


def test_provider_neutral_exact_receipt_requires_hash_and_indexed_placement(rig):
    service, path, row, artifact, placement, root = rig
    assert verify(service, path, row)
    placement["provider_id"] = "another-provider"
    assert not verify(service, path, row)
    placement["provider_id"] = "fictional-provider"
    placement["identity_id"] = "bob"
    assert not verify(service, path, row)
    placement["identity_id"] = "alice"
    row["credential_id"] = "other"
    assert not verify(service, path, row)
    row["credential_id"] = "manual"
    artifact["size"] += 1
    assert not verify(service, path, row)
    artifact["size"] -= 1
    path.write_bytes(b"fake but freshly inspected byteS")
    assert not verify(service, path, row)
    assert not path.is_symlink()


def test_untrusted_paths_and_symlink_chain_never_prove_placement(rig, tmp_path):
    service, path, row, _artifact, _placement, root = rig
    outside = tmp_path / "outside.epub"
    outside.write_bytes(path.read_bytes())
    assert not verify(service, outside, row)
    alias = root / "loan-2"
    alias.symlink_to(path.parent, target_is_directory=True)
    assert not verify(service, alias / "book.epub", row)
    leaf_link = root / "book.epub"
    leaf_link.symlink_to(path)
    assert not verify(service, leaf_link, row)
    assert outside.exists() and path.exists()  # no mutation


def test_inspection_failure_is_not_positive_or_logged_with_private_path(rig, monkeypatch):
    service, path, row, _, _, _ = rig
    def unavailable(_path):
        raise OSError("PRIVATE/secret/account")
    monkeypatch.setattr(evidence, "inspect_artifact", unavailable)
    assert not verify(service, path, row)
