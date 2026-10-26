"""#2565: cached SHA of a local book must be invalidated on a real file change.

Synthetic PDF bytes only; these checks never contact a source or mutate rights.
"""
from __future__ import annotations

import os

from cifereca.catalog import LocalCatalog
from cifereca.storage import Storage


def _case(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    book = root / "Book.pdf"
    book.write_bytes(b"%PDF-1.7\nORIGINAL_A\n")
    storage = Storage(tmp_path / "library.sqlite")
    catalog = LocalCatalog(storage, root)
    assert catalog.scan().cataloged == 1
    first = storage.placements()[0]
    return storage, catalog, book, first


def test_same_size_and_restored_mtime_does_not_reuse_old_sha(tmp_path):
    storage, catalog, book, previous = _case(tmp_path)
    before = book.stat()
    replacement = b"%PDF-1.7\nREPLACED_B\n"
    assert len(replacement) == before.st_size
    book.write_bytes(replacement)
    os.utime(book, ns=(before.st_atime_ns, before.st_mtime_ns))
    after = book.stat()
    assert after.st_size == before.st_size
    assert after.st_mtime_ns == before.st_mtime_ns
    assert after.st_ctime_ns != before.st_ctime_ns

    result = catalog.scan()

    assert result.cataloged == 1 and result.invalid == 0
    current = storage.placement_get(previous["placement_id"])
    assert current["path"] == previous["path"]
    assert current["artifact_id"] != previous["artifact_id"]
    assert current["metadata"]["file_ctime_ns"] == book.stat().st_ctime_ns
    assert current["metadata"]["file_inode"] == book.stat().st_ino
    assert storage.entitlements() == []


def test_new_inode_same_size_and_mtime_forces_sha_refresh(tmp_path):
    storage, catalog, book, previous = _case(tmp_path)
    before = book.stat()
    swapped = book.with_name("new-inode")
    swapped.write_bytes(b"%PDF-1.7\nREPLACED_B\n")
    assert swapped.stat().st_size == before.st_size
    os.utime(swapped, ns=(before.st_atime_ns, before.st_mtime_ns))
    os.replace(swapped, book)

    result = catalog.scan()

    assert result.cataloged == 1
    assert storage.placement_get(previous["placement_id"])["artifact_id"] != previous["artifact_id"]


def test_unchanged_file_reuses_sha_without_second_hash(tmp_path, monkeypatch):
    storage, catalog, book, previous = _case(tmp_path)
    called = []
    original = LocalCatalog.sha256

    def tracked(path):
        called.append(str(path))
        return original(path)

    monkeypatch.setattr(LocalCatalog, "sha256", staticmethod(tracked))
    result = catalog.scan()
    assert result.cataloged == 1
    assert called == []
    assert storage.placement_get(previous["placement_id"])["artifact_id"] == previous["artifact_id"]


def test_old_cache_without_fingerprint_is_upgraded_after_rehash(tmp_path, monkeypatch):
    storage, catalog, book, previous = _case(tmp_path)
    meta = dict(previous["metadata"])
    for key in ("file_inode", "file_device", "file_ctime_ns"):
        meta.pop(key)
    storage.placement_put({**previous, "metadata": meta})
    calls = []
    original = LocalCatalog.sha256

    def tracked(path):
        calls.append(path)
        return original(path)

    monkeypatch.setattr(LocalCatalog, "sha256", staticmethod(tracked))
    result = catalog.scan()

    assert result.cataloged == 1
    assert calls == [book]
    current = storage.placement_get(previous["placement_id"])
    assert current["artifact_id"] == previous["artifact_id"]
    assert current["metadata"]["file_inode"] == book.stat().st_ino
    assert current["metadata"]["file_ctime_ns"] == book.stat().st_ctime_ns
