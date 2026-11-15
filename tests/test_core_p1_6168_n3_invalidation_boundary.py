"""#6168: the shared N3 loader must never delete files outside its caches."""
from __future__ import annotations

from pathlib import Path
import hashlib

import pytest

from cifereca.epub_metadata import cover_cache_dir
from cifereca.gui.cover_loader import CoverLoader


PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 40
DIGEST = "a" * 64


@pytest.fixture
def loader(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    cover = CoverLoader(max_workers=2)
    try:
        yield cover, tmp_path
    finally:
        cover.close()


def test_only_source_url_preview_and_epub_cache_names_can_be_invalidated(loader):
    cover, tmp_path = loader
    original = cover._path("https://covers.example.org/one.jpg")
    preview = cover.preview_root / (DIGEST + ".png")
    epub_root = cover_cache_dir()
    epub_root.mkdir(parents=True, exist_ok=True)
    embedded = epub_root / (DIGEST + ".png")

    for name in (original, preview, embedded):
        name.write_bytes(PNG)
        assert cover._owned_n3_path(name)
        cover.invalidate(name, decode_failure=True)
        assert not name.exists()
    assert cover.stats()["decode_failed"] == 3

    # This path has a correctly shaped media filename but belongs to no N3
    # directory. No caller can convert its decodability into deletion rights.
    outside = tmp_path / (DIGEST + ".png")
    outside.write_bytes(PNG)
    cover.invalidate(outside, decode_failure=True)
    assert outside.read_bytes() == PNG
    assert cover.stats()["decode_failed"] == 3


@pytest.mark.parametrize("relative", [
    "a" * 64 + ".tmp",
    "a" * 64 + ".json",
    "a" * 64 + ".jpg",
    "book-cover.png",
    "../" + "a" * 64 + ".img",
])
def test_non_cache_or_non_owned_filename_is_not_unlinked(loader, relative):
    cover, tmp_path = loader
    path = cover.root / relative
    if relative.startswith(".."):
        path = tmp_path / ("a" * 64 + ".img")
    path.write_bytes(b"must survive")
    cover.invalidate(path)
    assert path.read_bytes() == b"must survive"
    assert cover.stats()["cache_invalid"] == 0


def test_symlink_leaf_is_unlinked_not_followed_to_foreign_file(loader):
    cover, tmp_path = loader
    foreign = tmp_path / "private-epub.epub"
    foreign.write_bytes(b"private source must survive")
    leaf = cover.root / (DIGEST + ".img")
    leaf.symlink_to(foreign)
    assert leaf.is_symlink()
    cover.invalidate(leaf, decode_failure=True)
    assert not leaf.is_symlink()
    assert foreign.read_bytes() == b"private source must survive"
    assert cover.stats()["decode_failed"] == 1


def test_symlinked_cache_subdirectory_cannot_redirect_invalidation(loader):
    cover, tmp_path = loader
    external = tmp_path / "external-cache"
    external.mkdir()
    original = cover.preview_root
    original.rmdir()
    original.symlink_to(external, target_is_directory=True)
    victim = external / (DIGEST + ".png")
    victim.write_bytes(PNG)
    cover.invalidate(original / victim.name, decode_failure=True)
    assert victim.read_bytes() == PNG
    assert cover.stats()["decode_failed"] == 0


def test_untrusted_string_and_unresolvable_parent_are_noop(loader):
    cover, tmp_path = loader
    path = tmp_path / "private.epub"
    path.write_bytes(b"book")
    cover.invalidate(str(path))
    cover.invalidate(tmp_path / "does-not-exist" / (DIGEST + ".png"))
    cover.invalidate(None)
    assert path.read_bytes() == b"book"
    assert cover.stats()["cache_invalid"] == 0
