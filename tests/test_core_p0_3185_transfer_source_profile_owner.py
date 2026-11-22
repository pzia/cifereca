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


@pytest.mark.parametrize("kind", ["stage", "bundle", "nested_bundle"])
def test_private_derived_stage_is_not_an_ordinary_send_source(tmp_path, kind, monkeypatch):
    from cifereca.core.derived_variant_region_paths import PRIVATE_DERIVED_REGION
    from cifereca.core import library_mutation
    service = _service(tmp_path)
    root = tmp_path / "EBooks"
    profile = root / "alice"
    region = profile / PRIVATE_DERIVED_REGION
    try:
        if kind == "stage":
            source = region / "stage.epub"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"private stage")
            witness = source
        else:
            source = (region if kind == "bundle" else profile / "local") / "Audio"
            source.mkdir(parents=True)
            (source / "track.mp3").write_bytes(b"audio")
            (source / "play.m3u8").write_text("#EXTM3U\ntrack.mp3\n")
            write_bundle_manifest(source, title="Audio", playlist="play.m3u8")
            witness = source / "track.mp3"
            if kind == "nested_bundle":
                hidden = source / PRIVATE_DERIVED_REGION
                hidden.mkdir()
                witness = hidden / "stage.epub"
                witness.write_bytes(b"private stage")
        placement = _placement(service, source, artifact_id="sha256:legacy")
        baseline, data = service.storage.placements(), witness.read_bytes()
        monkeypatch.setattr(library_mutation, "_owned_transfer_digest",
                            lambda *_args: pytest.fail("private stage hashed"))
        monkeypatch.setattr(library_mutation, "load_bundle_manifest",
                            lambda *_args, **_kw: pytest.fail("private bundle opened"))
        with pytest.raises(ProviderConfigurationError, match="Zone privée"):
            service.materialize_to_identity(placement, "bob")
        assert not (root / "bob" / "Acquisitions").exists()
        assert service.storage.placements() == baseline
        assert witness.read_bytes() == data
    finally:
        service.close()


@pytest.mark.parametrize("side", ["source", "destination"])
def test_direct_canonical_transfer_refuses_reserved_region_before_read(tmp_path, side, monkeypatch):
    from cifereca.core import canonical_transfer
    from cifereca.core.derived_variant_region_paths import PRIVATE_DERIVED_REGION
    from cifereca.locking import InterProcessLock
    service = _service(tmp_path)
    root = tmp_path / "EBooks"
    source = root / "alice" / (PRIVATE_DERIVED_REGION if side == "source" else "local") / "book.pdf"
    target = root / "bob" / (PRIVATE_DERIVED_REGION if side == "destination" else "Acquisitions") / "book.pdf"
    for path in (source, target):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"%PDF-1.7\nfixture")
    lock_path = tmp_path / "transfer.lock"
    monkeypatch.setattr(canonical_transfer, "acquisition_lock_file", lambda: lock_path)
    monkeypatch.setattr(canonical_transfer, "_physical_target",
                        lambda *_args: pytest.fail("private path opened"))
    before = service.storage.placements()
    try:
        with InterProcessLock(lock_path) as lock:
            with pytest.raises(ValueError, match="canonical_transfer_private_derived_region"):
                canonical_transfer.publish_completed_local_transfer(
                    service.storage, library_root=root, from_identity_id="alice",
                    to_identity_id="bob", from_path=source, to_path=target,
                    transfer_id="fixture-transfer", transfer_receipt_ref="fixture-receipt",
                    explicitly_authorized=True, shared_lock=lock)
        assert service.storage.placements() == before
        assert source.read_bytes() == target.read_bytes() == b"%PDF-1.7\nfixture"
    finally:
        service.close()
