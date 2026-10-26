"""#2565: local-file hashing must never attest replaced/symlinked bytes.

Synthetic PDF files only; no provider access or entitlement mutation.
"""
from __future__ import annotations

import os

import pytest

from cifereca.catalog import LocalCatalog
from cifereca.storage import Storage


def _case(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    path = root / "Book.pdf"
    path.write_bytes(b"%PDF-1.7\nORIGINAL_A\n")
    storage = Storage(tmp_path / "library.sqlite3")
    catalog = LocalCatalog(storage, root)
    assert catalog.scan().cataloged == 1
    placement = storage.placements()[0]
    return storage, catalog, path, placement


def _force_hash(storage, placement):
    # Simulate a legacy placement with no reusable fingerprint. This forces
    # exactly one fresh read, not an invented content change at scan start.
    storage.placement_put({**placement, "metadata": {}})


def test_descriptor_hash_rejects_link_to_external_file(tmp_path):
    storage, catalog, path, placement = _case(tmp_path)
    outside = tmp_path / "OUTSIDE.pdf"
    outside.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(outside)
    with pytest.raises(OSError):
        LocalCatalog.sha256(path)
    assert storage.placement_get(placement["placement_id"])["artifact_id"] == placement["artifact_id"]
    assert outside.read_bytes() == b"%PDF-1.7\nORIGINAL_A\n"


@pytest.mark.parametrize("mutation", ["rewrite", "replace_inode", "symlink"])
def test_change_between_hash_and_publication_aborts_without_new_artifact(
    tmp_path, monkeypatch, mutation,
):
    storage, catalog, path, placement = _case(tmp_path)
    _force_hash(storage, placement)
    original = LocalCatalog.sha256
    outside = tmp_path / "OUTSIDE.pdf"

    def mutate_after_read(book):
        digest = original(book)
        if mutation == "rewrite":
            book.write_bytes(b"%PDF-1.7\nREPLACED_B\n")
        elif mutation == "replace_inode":
            replacement = book.with_name("replacement.tmp")
            replacement.write_bytes(b"%PDF-1.7\nREPLACED_B\n")
            before = book.stat()
            os.utime(replacement, ns=(before.st_atime_ns, before.st_mtime_ns))
            os.replace(replacement, book)
        else:
            outside.write_bytes(b"%PDF-1.7\nREPLACED_B\n")
            book.unlink()
            book.symlink_to(outside)
        return digest

    monkeypatch.setattr(LocalCatalog, "sha256", staticmethod(mutate_after_read))
    with pytest.raises(OSError, match="catalog_scan_file_changed"):
        catalog.scan()
    assert storage.placement_get(placement["placement_id"])["artifact_id"] == placement["artifact_id"]
    assert len(storage.artifacts()) == 1
    assert storage.entitlements() == []
    if mutation == "symlink":
        assert path.is_symlink()
        assert outside.read_bytes() == b"%PDF-1.7\nREPLACED_B\n"


def test_normal_local_scan_still_reuses_unchanged_file(tmp_path, monkeypatch):
    storage, catalog, path, placement = _case(tmp_path)
    def forbid(_path):
        pytest.fail("same inode/device/ctime is already verified by cache")
    monkeypatch.setattr(LocalCatalog, "sha256", staticmethod(forbid))
    result = catalog.scan()
    assert result.cataloged == 1 and result.invalid == 0
    assert storage.placement_get(placement["placement_id"])["artifact_id"] == placement["artifact_id"]
    assert path.read_bytes() == b"%PDF-1.7\nORIGINAL_A\n"


def test_modified_pdf_on_subsequent_complete_scan_gets_new_hash(tmp_path):
    storage, catalog, path, placement = _case(tmp_path)
    path.write_bytes(b"%PDF-1.7\nREPLACED_B\n")
    result = catalog.scan()
    assert result.cataloged == 1
    assert storage.placement_get(placement["placement_id"])["artifact_id"] != placement["artifact_id"]
    assert len(storage.artifacts()) == 2
