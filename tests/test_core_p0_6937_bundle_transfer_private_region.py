"""#6937: transfers must not traverse private derived staging as a bundle."""

from pathlib import Path

import pytest

from cifereca.core import library_mutation
from cifereca.core.derived_variant_region_paths import PRIVATE_DERIVED_REGION
from cifereca.plugin_api import write_bundle_manifest
from cifereca.providers import ProviderConfigurationError
from tests.test_core0925_open_destinations import _placement, _service


def test_bundle_walk_rejects_reserved_directory_before_scandir(tmp_path, monkeypatch):
    root = tmp_path / "album"
    private = root / "disc" / PRIVATE_DERIVED_REGION
    private.mkdir(parents=True)
    stage = private / "stage.mp3"
    stage.write_bytes(b"private stage")
    (root / "ordinary.mp3").write_bytes(b"ordinary")
    original_scandir = library_mutation.os.scandir

    def guarded_scandir(path):
        if not isinstance(path, int):
            assert PRIVATE_DERIVED_REGION not in Path(path).parts
        return original_scandir(path)

    monkeypatch.setattr(library_mutation.os, "scandir", guarded_scandir)
    assert library_mutation._bundle_transfer_exclusion(root) == "private"
    assert stage.read_bytes() == b"private stage"
    assert (root / "ordinary.mp3").read_bytes() == b"ordinary"


def test_bundle_walk_near_name_remains_ordinary_and_symlinks_are_rejected(tmp_path):
    root = tmp_path / "album"
    near = root / (PRIVATE_DERIVED_REGION + "-backup")
    near.mkdir(parents=True)
    file = near / "track.mp3"
    file.write_bytes(b"ordinary")
    assert library_mutation._bundle_transfer_exclusion(root) is None
    assert file.read_bytes() == b"ordinary"
    external = tmp_path / "external.mp3"
    external.write_bytes(b"not local")
    (near / "foreign.mp3").symlink_to(external)
    assert library_mutation._bundle_transfer_exclusion(root) == "symlink"
    assert external.read_bytes() == b"not local"


def test_source_bundle_with_reserved_stage_cannot_send_or_mutate(tmp_path):
    service = _service(tmp_path)
    root = tmp_path / "EBooks"
    bundle = root / "alice" / "local" / "Audio"
    bundle.mkdir(parents=True)
    (bundle / "track.mp3").write_bytes(b"synthetic track")
    write_bundle_manifest(bundle, title="Synthetic audio")
    private = bundle / PRIVATE_DERIVED_REGION
    private.mkdir()
    stage = private / "unpublished.mp3"
    stage.write_bytes(b"unpublished")
    placement_id = _placement(
        service, bundle, identity_id="alice", artifact_id="sha256:bundle",
    )
    try:
        before = service.storage.placements(), service.storage.entitlements()
        with pytest.raises(
            ProviderConfigurationError,
            match="Zone privée de publication dérivée",
        ):
            service.materialize_to_identity(placement_id, "bob")
        assert service.storage.placements() == before[0]
        assert service.storage.entitlements() == before[1]
        assert not (root / "bob" / "Acquisitions" / "Audio").exists()
        assert stage.read_bytes() == b"unpublished"
    finally:
        service.close()


def test_bundle_walk_inaccessible_directory_fails_closed(tmp_path, monkeypatch):
    root = tmp_path / "album"
    root.mkdir()

    def inaccessible(_root, **options):
        options["onerror"](PermissionError("synthetic inaccessible child"))
        yield

    monkeypatch.setattr(library_mutation.os, "walk", inaccessible)
    with pytest.raises(OSError, match="bundle_walk_incomplete"):
        library_mutation._bundle_transfer_exclusion(root)
