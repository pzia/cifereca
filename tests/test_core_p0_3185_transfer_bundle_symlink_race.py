"""P0 #3185: a bundle member swapped to Bob's symlink must not be copied.

The manifest and all initial paths are verified BEFORE the catalogue read.
At that boundary another actor substitutes a file or directory link leading
into Bob's profile, with identical bytes: an ordinary copytree(following
symlinks) would silently accept the foreign contents. Tests use only local
synthetic bundles and disposable SQLite.
"""
import shutil

import pytest

from cifereca.plugin_api import write_bundle_manifest
from cifereca.providers import ProviderConfigurationError
from tests.test_core0925_open_destinations import _placement, _service


@pytest.mark.parametrize("replacement", ["file", "directory"])
def test_bundle_symlink_injected_after_manifest_never_reads_foreign_profile(
    tmp_path, monkeypatch, replacement,
):
    service = _service(tmp_path)
    root = tmp_path / "EBooks"
    source = root / "alice" / "local" / "Audio"
    disk = source / "disc"
    disk.mkdir(parents=True)
    track = disk / "track.mp3"
    contents = b"same synthetic private audiobook content"
    track.write_bytes(contents)
    (source / "play.m3u8").write_text(
        "#EXTM3U\ndisc/track.mp3\n", encoding="utf-8",
    )
    write_bundle_manifest(source, title="Alice synthetic audio", playlist="play.m3u8")
    pid = _placement(
        service, source, identity_id="alice", artifact_id="sha256:bundle",
    )
    foreign_disk = root / "bob" / "local" / "other-audio"
    foreign_disk.mkdir(parents=True)
    foreign_track = foreign_disk / "track.mp3"
    foreign_track.write_bytes(contents)

    original_catalogue_read = service.storage.catalog_entries
    injected = []

    def swap_member_after_manifest(*args, **kwargs):
        if not injected:
            if replacement == "file":
                track.unlink()
                track.symlink_to(foreign_track)
            else:
                shutil.rmtree(disk)
                disk.symlink_to(foreign_disk, target_is_directory=True)
            injected.append(True)
        return original_catalogue_read(*args, **kwargs)

    monkeypatch.setattr(service.storage, "catalog_entries", swap_member_after_manifest)
    try:
        with pytest.raises(
            ProviderConfigurationError, match="Bundle copié non vérifié",
        ):
            service.materialize_to_identity(pid, "bob")
        assert injected == [True]
        assert foreign_track.read_bytes() == contents
        assert (source / "play.m3u8").is_file()
        assert not (root / "bob" / "Acquisitions" / "Audio").exists()
        assert not any(
            row.get("identity_id") == "bob"
            for row in service.storage.placements()
        )
        assert service.storage.entitlements() == []
    finally:
        service.close()
