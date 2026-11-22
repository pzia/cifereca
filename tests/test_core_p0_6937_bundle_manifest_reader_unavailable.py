"""#6937: unavailable legacy bundle references fail closed, never a raw FS error.

Synthetic bundles only; no cleanup, publication, network or protected stage read.
"""
from pathlib import Path

import pytest

from cifereca.plugin_api import BundleError
from cifereca.plugin_api import bundles


def _bundle(tmp_path):
    root = tmp_path / "Audio"
    root.mkdir()
    member = root / "track.mp3"
    member.write_bytes(b"ordinary synthetic track")
    bundles.write_bundle_manifest(root, title="Synthetic")
    return root, member


@pytest.mark.parametrize("verify", [True, False])
def test_cyclic_member_alias_never_raises_raw_resolution_error(tmp_path, verify):
    root, member = _bundle(tmp_path)
    member.unlink()
    member.symlink_to("track.mp3")
    with pytest.raises(BundleError, match="bundle member path unavailable"):
        bundles.load_bundle_manifest(root, verify=verify)
    assert member.is_symlink()


@pytest.mark.parametrize("verify", [True, False])
def test_cyclic_root_alias_never_opens_a_manifest(tmp_path, verify):
    root = tmp_path / "Audio"
    root.symlink_to("Audio")
    with pytest.raises(BundleError, match="bundle root unavailable"):
        bundles.load_bundle_manifest(root, verify=verify)
    assert root.is_symlink()


@pytest.mark.parametrize("verify", [True, False])
def test_member_unavailable_after_validation_is_explicit_bundle_error(
    tmp_path, monkeypatch, verify,
):
    root, member = _bundle(tmp_path)
    original = Path.is_file

    def unavailable(path):
        if path == member:
            raise PermissionError("PRIVATE_FS_ERROR")
        return original(path)

    monkeypatch.setattr(Path, "is_file", unavailable)
    with pytest.raises(BundleError, match="bundle member unavailable") as error:
        bundles.load_bundle_manifest(root, verify=verify)
    assert "PRIVATE_FS_ERROR" not in str(error.value)
    assert member.read_bytes() == b"ordinary synthetic track"


def test_hash_unavailable_does_not_claim_valid_ordinary_bundle(tmp_path, monkeypatch):
    root, member = _bundle(tmp_path)

    def unavailable(_path):
        raise OSError("PRIVATE_FS_ERROR")

    monkeypatch.setattr(bundles, "_sha256_file", unavailable)
    with pytest.raises(BundleError, match="bundle member unavailable") as error:
        bundles.load_bundle_manifest(root, verify=True)
    assert "PRIVATE_FS_ERROR" not in str(error.value)
    assert member.read_bytes() == b"ordinary synthetic track"


def test_valid_ordinary_bundle_read_preserves_member_and_manifest(tmp_path):
    root, member = _bundle(tmp_path)
    manifest = root / bundles.BUNDLE_MANIFEST_NAME
    before = (member.read_bytes(), manifest.read_bytes())
    assert len(bundles.load_bundle_manifest(root, verify=True).members) == 1
    assert (member.read_bytes(), manifest.read_bytes()) == before
