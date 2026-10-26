"""P0 #2565: targeted indexing may not publish bytes replaced after hashing.

Only local synthetic PDFs and temporary SQLite; no provider/auth/DRM I/O.
"""
from __future__ import annotations

import os

import pytest

from cifereca.catalog import LocalCatalog
from cifereca.storage import Storage


@pytest.mark.parametrize("mutation", ["rewrite", "replace_inode", "symlink"])
def test_targeted_index_rejects_replacement_after_hash(
    tmp_path, monkeypatch, mutation,
):
    root = tmp_path / "library"
    root.mkdir()
    path = root / "Book.pdf"
    original_bytes = b"%PDF-1.7\nORIGINAL_A\n"
    path.write_bytes(original_bytes)
    store = Storage(tmp_path / "state.sqlite")
    catalogue = LocalCatalog(store, root)
    first = catalogue.index_path(
        path, identity_id="owner-a", provider_id="kobo", item_id="edition-a"
    )
    assert first["status"] == "indexed"
    placement_before = store.placement_for_path(path)
    catalog_before = store.catalog_entries()
    artifacts_before = store.artifacts()
    real_sha = LocalCatalog.sha256
    external = tmp_path / "OUTSIDE.pdf"

    def replaced_after_hash(book):
        digest = real_sha(book)
        replacement_bytes = b"%PDF-1.7\nREPLACED_B\n"
        if mutation == "rewrite":
            # Same byte count; also restore mtime to prove ctime matters.
            prior = book.stat()
            book.write_bytes(replacement_bytes)
            os.utime(book, ns=(prior.st_atime_ns, prior.st_mtime_ns))
        elif mutation == "replace_inode":
            prior = book.stat()
            replacement = book.with_suffix(".part")
            replacement.write_bytes(replacement_bytes)
            os.utime(replacement, ns=(prior.st_atime_ns, prior.st_mtime_ns))
            os.replace(replacement, book)
        else:
            external.write_bytes(replacement_bytes)
            book.unlink()
            book.symlink_to(external)
        return digest

    monkeypatch.setattr(LocalCatalog, "sha256", staticmethod(replaced_after_hash))
    with pytest.raises(OSError, match="catalog_index_file_changed"):
        catalogue.index_path(path)

    assert store.placement_for_path(path) == placement_before
    assert store.catalog_entries() == catalog_before
    assert store.artifacts() == artifacts_before
    assert store.acquisitions() == []
    if mutation == "symlink":
        assert path.is_symlink()
        assert external.read_bytes() == b"%PDF-1.7\nREPLACED_B\n"


def test_targeted_index_of_unchanged_file_remains_available(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    path = root / "Book.pdf"
    path.write_bytes(b"%PDF-1.7\nSynthetic unchanged\n")
    store = Storage(tmp_path / "state.sqlite")
    catalogue = LocalCatalog(store, root)
    first = catalogue.index_path(path)
    second = catalogue.index_path(path)
    assert first["status"] == second["status"] == "indexed"
    assert first["artifact_id"] == second["artifact_id"]
    assert store.placement_for_path(path)["state"] == "present"
    assert len(store.artifacts()) == 1


def test_first_targeted_index_cannot_create_false_artifact_on_race(
    tmp_path, monkeypatch,
):
    root = tmp_path / "library"
    root.mkdir()
    path = root / "New.pdf"
    path.write_bytes(b"%PDF-1.7\nORIGINAL_A\n")
    store = Storage(tmp_path / "state.sqlite")
    catalogue = LocalCatalog(store, root)
    real_sha = LocalCatalog.sha256

    def replaced_after_hash(book):
        digest = real_sha(book)
        book.write_bytes(b"%PDF-1.7\nREPLACED_B\n")
        return digest

    monkeypatch.setattr(LocalCatalog, "sha256", staticmethod(replaced_after_hash))
    with pytest.raises(OSError, match="catalog_index_file_changed"):
        catalogue.index_path(path)
    assert store.placements() == []
    assert store.artifacts() == []
    assert store.catalog_entries() == []
