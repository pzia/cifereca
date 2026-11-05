"""#3185: a local send may read only the source Placement owner's bytes.

Use the live LibraryService/SQLite transfer entrypoint and disposable files.
No network, remote rights, mutation or destructive repair is authorized.
"""
from pathlib import Path

import pytest

from cifereca.plugin_api import write_bundle_manifest
from cifereca.providers import ProviderConfigurationError
from tests.test_core0925_open_destinations import _placement, _service


@pytest.mark.parametrize("physical_scope", ["foreign_profile", "unscoped"])
@pytest.mark.parametrize("bundle", [False, True])
def test_misowned_source_cannot_be_sent_before_any_recipient_write(
    tmp_path, physical_scope, bundle,
):
    service = _service(tmp_path)
    root = tmp_path / "EBooks"
    folder = (
        root / "bob" / "local" if physical_scope == "foreign_profile"
        else root / "unscoped" / "local"
    )
    try:
        if bundle:
            source = folder / "Audio"
            source.mkdir(parents=True)
            (source / "track.mp3").write_bytes(b"original-audio")
            (source / "play.m3u8").write_text(
                "#EXTM3U\ntrack.mp3\n", encoding="utf-8",
            )
            write_bundle_manifest(source, title="Other profile audio", playlist="play.m3u8")
        else:
            source = folder / "Book.epub"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"original-legacy-book")

        # SQLite claims Alice owns a physical path in Bob's directory (or
        # directly in the library). The legacy source has no canonical SHA.
        placement = _placement(
            service, source, identity_id="alice",
            artifact_id="sha256:bundle" if bundle else "sha256:legacy",
        )
        original = (
            (source / "track.mp3").read_bytes()
            if bundle else source.read_bytes()
        )
        with pytest.raises(ProviderConfigurationError, match="profil propriétaire"):
            service.materialize_to_identity(placement, "bob")

        assert not (root / "bob" / "Acquisitions").exists()
        assert service.storage.placement_get(placement)["identity_id"] == "alice"
        assert not any(
            row.get("identity_id") == "bob" for row in service.storage.placements()
        )
        assert service.storage.catalog_entries() == []
        assert (
            (source / "track.mp3").read_bytes()
            if bundle else source.read_bytes()
        ) == original
    finally:
        service.close()


def test_existing_same_owner_legacy_transfer_still_succeeds(tmp_path):
    service = _service(tmp_path)
    source = tmp_path / "EBooks" / "alice" / "local" / "Book.epub"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"valid-owned-legacy-book")
    placement = _placement(service, source, artifact_id="sha256:legacy")
    try:
        result = service.materialize_to_identity(placement, "bob")
        assert Path(result["path"]).read_bytes() == source.read_bytes()
        assert service.storage.placement_get(result["placement_id"])["identity_id"] == "bob"
    finally:
        service.close()
