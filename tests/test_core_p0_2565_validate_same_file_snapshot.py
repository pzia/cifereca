"""P0 #2565: local validation and stored SHA must describe identical bytes.

Only synthetic PDF paths and SQLite; no provider/network or user files.
"""
from __future__ import annotations

import os

import pytest

from cifereca.catalog import LocalCatalog
from cifereca.storage import Storage


@pytest.mark.parametrize("entry_point", ("targeted", "scan"))
def test_file_replaced_after_format_validation_never_becomes_present(
    tmp_path, monkeypatch, entry_point,
):
    root = tmp_path / "library"
    root.mkdir()
    book = root / "book.pdf"
    original = b"%PDF-1.7\nVALID CONTENT\n"
    book.write_bytes(original)
    mtime = book.stat().st_mtime_ns
    replacement = root / "untrusted.part"
    replacement.write_bytes(b"NOT-A-PDF\nNEW BYTES!!!\n")
    assert replacement.stat().st_size == book.stat().st_size
    os.utime(replacement, ns=(mtime, mtime))

    storage = Storage(tmp_path / "library.sqlite")
    catalog = LocalCatalog(storage, root)
    validate = catalog._validate

    def replace_after_validation(path, media_format):
        result = validate(path, media_format)
        assert result == (True, None)
        os.replace(replacement, path)
        return result

    monkeypatch.setattr(catalog, "_validate", replace_after_validation)

    with pytest.raises(OSError, match="catalog_(index|scan)_file_changed"):
        if entry_point == "targeted":
            catalog.index_path(book, identity_id="account-A", provider_id="kobo")
        else:
            catalog.scan()

    assert book.read_bytes() != original
    assert storage.placements() == []
    assert storage.catalog_entries() == []
    assert storage.artifacts() == []


@pytest.mark.parametrize("entry_point", ("targeted", "scan"))
def test_stable_pdf_still_indexes_normally(tmp_path, entry_point):
    root = tmp_path / "library"
    root.mkdir()
    book = root / "book.pdf"
    book.write_bytes(b"%PDF-1.7\nVALID\n")
    storage = Storage(tmp_path / "library.sqlite")
    catalog = LocalCatalog(storage, root)

    if entry_point == "targeted":
        assert catalog.index_path(book)["status"] == "indexed"
    else:
        assert catalog.scan().cataloged == 1

    placement = storage.placement_for_path(book)
    assert placement["state"] == "present"
    assert placement["artifact_id"].startswith("sha256:")
