"""P1 #6168: EPUB N1 metadata survives N3 physical-image eviction."""
from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from cifereca import catalog, epub_metadata
from cifereca.core.book_cover_projection import CoverDocumentScope
from cifereca.core.epub_cover_materialization import EpubCoverPhysicalCache
from cifereca.core.epub_cover_source import project_exact_embedded_cover
from tests.test_epub_cover_contract import _write_epub


class Storage:
    def __init__(self, artifact):
        self.artifact = artifact
        self.artifact_puts = []
        self.catalog_puts = []

    def artifact_get(self, key):
        assert key == self.artifact["artifact_id"]
        return self.artifact

    def artifact_put(self, artifact):
        self.artifact_puts.append(artifact)
        self.artifact = artifact

    def catalog_entry_put(self, entry):
        self.catalog_puts.append(entry)


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    book = tmp_path / "local.epub"
    content = _write_epub(book, epub3=True)
    sha = hashlib.sha256(content).hexdigest()
    proof = epub_metadata.inspect_epub(book)
    assert proof["epub_cover_status"] == "extracted"
    artifact_id = "sha256:" + sha
    storage = Storage({
        "artifact_id": artifact_id, "sha256": sha,
        "metadata": proof.copy(),
    })
    entry = {
        "catalog_entry_id": "catalog:exact-local",
        "artifact_id": artifact_id,
        "local_state": "local", "media_format": "epub",
        "title": "Indexed", "author": "Author",
        "metadata": {"path": str(book)},
    }
    image = epub_metadata.cover_cache_dir() / proof["epub_cover"]["cache_name"]
    assert epub_metadata.valid_cached_cover_path(image)
    return book, sha, storage, entry, proof, image


def test_ordinary_enrichment_reuses_exact_n1_metadata_after_n3_miss(prepared, monkeypatch):
    _book, _sha, storage, entry, proof, image = prepared
    image.unlink()

    def forbidden(*_args, **_kwargs):
        pytest.fail("N3 image eviction must not rerun inspect_epub")

    monkeypatch.setattr(catalog, "inspect_epub", forbidden)
    host = SimpleNamespace(storage=storage)
    resulting = catalog.LocalCatalog._enrich_epub_entry(host, entry)
    assert resulting["metadata"]["epub_cover"] == proof["epub_cover"]
    assert resulting["metadata"]["epub_metadata_schema"] == proof["epub_metadata_schema"]
    assert storage.artifact_puts == []
    assert not image.exists()


def test_cold_n3_restores_exact_artifact_image_without_reparsing_n1(
    prepared, monkeypatch,
):
    book, sha, storage, entry, proof, image = prepared
    image.unlink()
    monkeypatch.setattr(
        catalog, "inspect_epub",
        lambda *_args, **_kwargs: pytest.fail("catalog must remain N1 cache HIT"),
    )
    catalog.LocalCatalog._enrich_epub_entry(SimpleNamespace(storage=storage), entry)
    scope = CoverDocumentScope(
        kind="placement", identity_id="alice",
        source_key="artifact:sha256:" + sha,
        artifact_id="sha256:" + sha, placement_id="placement:a",
    )
    selected = project_exact_embedded_cover(
        scope, revision=3, artifact_sha256=sha, artifact_metadata=proof,
    )
    assert selected is not None
    cache = EpubCoverPhysicalCache()
    try:
        restored = cache.request(
            selected.resource, source_path=book,
        ).result(timeout=10)
        assert restored == image.resolve().as_uri()
        assert epub_metadata.valid_cached_cover_path(image)
    finally:
        cache.close()


def test_explicit_force_scan_still_reinspects_once_and_republishes_n3(prepared, monkeypatch):
    _book, _sha, storage, entry, proof, image = prepared
    image.unlink()
    called = []
    original = catalog.inspect_epub

    def watched(path, **kwargs):
        called.append(path)
        return original(path, **kwargs)

    monkeypatch.setattr(catalog, "inspect_epub", watched)
    host = SimpleNamespace(
        storage=storage, _force_covers=True, _cover_refresh_seen=set(),
    )
    catalog.LocalCatalog._enrich_epub_entry(host, entry)
    assert len(called) == 1
    assert storage.artifact_puts
    assert epub_metadata.valid_cached_cover_path(image)
    assert entry["artifact_id"] in host._cover_refresh_seen
    catalog.LocalCatalog._enrich_epub_entry(host, entry)
    assert len(called) == 1
    assert storage.artifact["metadata"]["epub_cover"] == proof["epub_cover"]


def test_epub_cache_schema_not_tied_to_source_image_presence():
    source = (Path(__file__).resolve().parents[1] /
              "src/cifereca/catalog.py").read_text(encoding="utf-8")
    assert "complete=cached.get('epub_metadata_schema')==EPUB_METADATA_SCHEMA and not force" in source
    assert "if cached.get('epub_cover') and not cached_cover_url(cached):complete=False" not in source
    assert "inspected=inspect_epub(path)" in source
