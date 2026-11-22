"""#6937: an existing bundle manifest cannot authorize reading Core stages.

Synthetic bytes only, no account, network, writer or physical cleanup.
"""
import hashlib
import json
from pathlib import Path

import pytest

from cifereca.core.derived_variant_region_paths import PRIVATE_DERIVED_REGION
from cifereca.plugin_api import BUNDLE_MANIFEST_NAME, BundleError
from cifereca.plugin_api import bundles


def _legacy_manifest(root, member):
    payload = {
        "manifest": 1, "kind": "cifereca.bundle",
        "media_format": "audiobook", "title": "Synthetic audio",
        "author": "", "playlist": "", "metadata": {},
        "members": [{
            "path": member, "sha256": hashlib.sha256(b"private bytes").hexdigest(),
            "size": len(b"private bytes"), "format": "mp3",
        }],
    }
    (root / BUNDLE_MANIFEST_NAME).write_text(json.dumps(payload), encoding="utf-8")


def _forbid_private_member_stat(monkeypatch):
    original = Path.is_file

    def guarded(path):
        assert PRIVATE_DERIVED_REGION not in path.parts, "private member was inspected"
        return original(path)

    monkeypatch.setattr(Path, "is_file", guarded)
    monkeypatch.setattr(
        bundles, "_sha256_file",
        lambda _path: pytest.fail("private bundle member was hashed"),
    )


@pytest.mark.parametrize("verify", [True, False])
def test_legacy_manifest_private_member_is_rejected_without_stat_or_hash(
    tmp_path, monkeypatch, verify,
):
    root = tmp_path / "ordinary-audio"
    private = root / "disc" / PRIVATE_DERIVED_REGION
    private.mkdir(parents=True)
    member = private / "unpublished.mp3"
    member.write_bytes(b"private bytes")
    _legacy_manifest(root, f"disc/{PRIVATE_DERIVED_REGION}/unpublished.mp3")
    before = (root / BUNDLE_MANIFEST_NAME).read_bytes()

    _forbid_private_member_stat(monkeypatch)
    with pytest.raises(BundleError, match="reserved Core derived region"):
        bundles.load_bundle_manifest(root, verify=verify)
    assert member.read_bytes() == b"private bytes"
    assert (root / BUNDLE_MANIFEST_NAME).read_bytes() == before


@pytest.mark.parametrize("verify", [True, False])
def test_manifest_symlink_alias_into_core_stage_is_refused(
    tmp_path, monkeypatch, verify,
):
    root = tmp_path / "audio"
    private = root / PRIVATE_DERIVED_REGION
    private.mkdir(parents=True)
    stage = private / "stage.mp3"
    stage.write_bytes(b"private bytes")
    alias = root / "alias.mp3"
    alias.symlink_to(stage)
    _legacy_manifest(root, "alias.mp3")

    _forbid_private_member_stat(monkeypatch)
    with pytest.raises(BundleError, match="reserved Core derived region"):
        bundles.load_bundle_manifest(root, verify=verify)
    assert alias.is_symlink()
    assert stage.read_bytes() == b"private bytes"


def test_manifest_root_inside_reserved_stage_is_refused_before_open(
    tmp_path, monkeypatch,
):
    root = tmp_path / "alice" / PRIVATE_DERIVED_REGION / "Audio"
    root.mkdir(parents=True)
    manifest = root / BUNDLE_MANIFEST_NAME
    manifest.write_text("{}", encoding="utf-8")
    original = Path.read_text

    def guarded(path, *args, **kwargs):
        assert PRIVATE_DERIVED_REGION not in path.parts, "private manifest was opened"
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded)
    with pytest.raises(BundleError, match="reserved Core derived region"):
        bundles.load_bundle_manifest(root)
    assert manifest.read_bytes() == b"{}"


def test_near_named_ordinary_member_remains_valid(tmp_path):
    root = tmp_path / "ordinary-audio"
    near = root / (PRIVATE_DERIVED_REGION + "-backup")
    near.mkdir(parents=True)
    member = near / "track.mp3"
    member.write_bytes(b"ordinary bytes")

    manifest = bundles.write_bundle_manifest(root, title="Synthetic audio")
    observed = bundles.load_bundle_manifest(root, verify=True)
    assert observed.bundle_sha256 == manifest.bundle_sha256
    assert [part.path for part in observed.members] == [
        PRIVATE_DERIVED_REGION + "-backup/track.mp3",
    ]
    assert member.read_bytes() == b"ordinary bytes"
