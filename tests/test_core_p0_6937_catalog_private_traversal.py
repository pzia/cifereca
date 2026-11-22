"""#6937: the ordinary catalogue must prune private stages *before* scandir."""
from pathlib import Path

import pytest

from cifereca import catalog
from cifereca.core.derived_variant_region_paths import PRIVATE_DERIVED_REGION
from cifereca.storage import Storage


def test_catalogue_never_enters_private_v1_region(tmp_path, monkeypatch):
    root = tmp_path / "library"
    private = root / "alice" / PRIVATE_DERIVED_REGION
    public = root / "alice" / (PRIVATE_DERIVED_REGION + "-archive")
    private.mkdir(parents=True)
    public.mkdir()
    stage = private / "stage.epub"
    user = public / "user.pdf"
    stage.write_bytes(b"confidential stage bytes")
    user.write_bytes(b"ordinary fixture bytes")
    real_scandir = catalog.os.scandir

    def guarded(path):
        # Covers both old pathlib.rglob and the replacement os.walk paths.
        if not isinstance(path, int):
            assert PRIVATE_DERIVED_REGION not in Path(path).parts, (
                "ordinary catalog entered Core-owned staging directory")
        return real_scandir(path)

    monkeypatch.setattr(catalog.os, "scandir", guarded)
    observed = list(catalog._ordinary_catalog_files(root))
    assert observed == [user]
    assert stage.read_bytes() == b"confidential stage bytes"
    assert user.read_bytes() == b"ordinary fixture bytes"

    storage = Storage(tmp_path / "library.sqlite")
    try:
        catalog.LocalCatalog(storage, root).scan()
        assert not any(PRIVATE_DERIVED_REGION in Path(p["path"]).parts
                       for p in storage.placements())
        assert stage.read_bytes() == b"confidential stage bytes"
    finally:
        storage.close()


def test_catalogue_rejects_direct_private_stage_root(tmp_path):
    region = tmp_path / "alice" / PRIVATE_DERIVED_REGION
    region.mkdir(parents=True)
    witness = region / "witness.epub"
    witness.write_bytes(b"private")
    with pytest.raises(OSError, match="catalog_scan_reserved_root"):
        list(catalog._ordinary_catalog_files(region))
    assert witness.read_bytes() == b"private"


def test_catalogue_recognizes_only_exact_reserved_directory_name(tmp_path):
    root = tmp_path / "library"
    sibling = root / (PRIVATE_DERIVED_REGION + "-backup")
    sibling.mkdir(parents=True)
    user = sibling / "book.epub"
    user.write_bytes(b"user")
    assert list(catalog._ordinary_catalog_files(root)) == [user]


def test_catalogue_scandir_error_does_not_prove_empty_directory(tmp_path, monkeypatch):
    root = tmp_path / "library"
    root.mkdir()
    real_walk = catalog.os.walk
    def unavailable(*args, **kwargs):
        raise OSError("synthetic child inaccessible")
        yield
    monkeypatch.setattr(catalog.os, "walk", unavailable)
    with pytest.raises(OSError, match="synthetic child inaccessible"):
        list(catalog._ordinary_catalog_files(root))
    monkeypatch.setattr(catalog.os, "walk", real_walk)
