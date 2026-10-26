"""#2847/#2565: catalog scan never treats linked foreign bytes as local provenance.

All paths, books and SQLite files in these cases are synthetic.
"""
from __future__ import annotations

from pathlib import Path

from cifereca.catalog import LocalCatalog
from cifereca.plugin_api import BUNDLE_MANIFEST_NAME
from cifereca.storage import Storage


def _case(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    storage = Storage(tmp_path / "catalog.sqlite")
    return storage, root


def test_symlinked_file_outside_library_is_never_adopted(tmp_path):
    storage, root = _case(tmp_path)
    private = tmp_path / "unrelated" / "outside.pdf"
    private.parent.mkdir()
    private.write_bytes(b"%PDF-1.7\nPRIVATE_EXTERNAL_BYTES\n")
    alias = root / "alien.pdf"
    alias.symlink_to(private)

    result = LocalCatalog(storage, root).scan()

    assert result.scanned == 1
    assert result.invalid == 1
    assert result.cataloged == 0
    assert storage.placements() == []
    assert storage.catalog_entries() == []
    assert private.read_bytes() == b"%PDF-1.7\nPRIVATE_EXTERNAL_BYTES\n"
    assert alias.is_symlink()


def test_linked_manifest_cannot_publish_bundle_contents(tmp_path):
    storage, root = _case(tmp_path)
    private = tmp_path / "outside-manifest.json"
    private.write_text('{"untrusted":true}', encoding="utf-8")
    alias = root / BUNDLE_MANIFEST_NAME
    alias.symlink_to(private)

    result = LocalCatalog(storage, root).scan()

    assert result.cataloged == 0
    assert storage.placements() == []
    assert private.read_text(encoding="utf-8") == '{"untrusted":true}'


def test_external_directory_link_is_not_treated_as_owned_subtree(tmp_path):
    storage, root = _case(tmp_path)
    private = tmp_path / "unrelated"
    private.mkdir()
    (private / "book.pdf").write_bytes(b"%PDF-1.7\nPRIVATE_EXTERNAL_BYTES\n")
    (root / "linked-directory").symlink_to(private, target_is_directory=True)

    LocalCatalog(storage, root).scan()

    assert storage.placements() == []
    assert storage.catalog_entries() == []


def test_ordinary_file_inside_library_still_indexes(tmp_path):
    storage, root = _case(tmp_path)
    book = root / "ordinary.pdf"
    book.write_bytes(b"%PDF-1.7\nOWNED_SYNTHETIC_BOOK\n")

    result = LocalCatalog(storage, root).scan()

    assert result.invalid == 0
    assert result.cataloged == 1
    assert len(storage.placements()) == 1
    assert storage.placements()[0]["path"] == str(book.resolve())


def test_explicit_root_symlink_remains_supported_without_inner_links(tmp_path):
    physical = tmp_path / "physical-library"
    physical.mkdir()
    (physical / "own.pdf").write_bytes(b"%PDF-1.7\nSYNTHETIC\n")
    root = tmp_path / "configured-library"
    root.symlink_to(physical, target_is_directory=True)
    storage = Storage(tmp_path / "catalog.sqlite")

    result = LocalCatalog(storage, root).scan()

    assert result.cataloged == 1
    assert len(storage.placements()) == 1
    assert storage.placements()[0]["path"] == str((physical / "own.pdf").resolve())
