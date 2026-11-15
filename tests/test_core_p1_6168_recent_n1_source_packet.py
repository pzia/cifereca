"""#6168 Récents: same exact Artifact+Placement N2 document, shared physical N3."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from cifereca import epub_metadata
from cifereca.core.local_cover_evidence import project_verified_local_cover_evidence
from cifereca.gui.recent_rows import project_recent_rows
from tests.test_epub_cover_contract import _write_epub


class Row:
    def __init__(self, *args):
        self.args = args


@pytest.fixture
def source(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    book = tmp_path / "known.epub"
    raw = _write_epub(book, epub3=True)
    sha = hashlib.sha256(raw).hexdigest()
    packet = project_verified_local_cover_evidence(
        identity_id="alice", book_key="artifact:sha256:" + sha,
        artifact_id="sha256:" + sha, placement_id="placement:a",
        artifact_sha256=sha, verified_path=book,
        artifact_metadata=epub_metadata.inspect_epub(book),
    )
    assert packet is not None and packet.embedded is not None
    item = {
        "placement_id": packet.scope.placement_id,
        "artifact_id": packet.scope.artifact_id,
        "identity_id": "alice", "provider_id": "local",
        "title": "Known", "author": "Author",
        "thumbnail_url": "https://public.example.org/known.png",
        "_exact_local_cover_evidence": packet,
    }
    return packet, item


def project(item):
    rows, objects = project_recent_rows(
        [item], row_factory=Row,
        provider_display_name=lambda _source: "Source",
    )
    assert objects[rows[0].args[0]] is item
    return rows[0]


def test_recent_uses_document_source_key_not_its_placement_list_key(source):
    packet, item = source
    row = project(item)
    assert row.args[0] == "placement:a"
    assert row._cover_document_ref == packet.scope
    assert row._cover_document_ref.source_key.startswith("artifact:sha256:")
    assert row._cover_local_evidence is packet
    assert row._public_row_content[-1] is packet


@pytest.mark.parametrize("alter", [
    {"identity_id": "bob"},
    {"artifact_id": "sha256:" + "b" * 64},
    {"placement_id": "placement:b"},
    {"_exact_local_cover_evidence": object()},
])
def test_recent_does_not_attach_foreign_or_unverified_packet(source, alter):
    _packet, item = source
    row = project({**item, **alter})
    assert not hasattr(row, "_cover_document_ref")


def test_recent_unqualified_preserves_original_row_shape(source):
    _packet, item = source
    row = project({k: v for k, v in item.items()
                   if k != "_exact_local_cover_evidence"})
    assert row.args[0] == "placement:a"
    assert len(row.args) == 6
    assert not hasattr(row, "_public_row_content")


def test_real_recent_library_share_scope_and_survive_physical_n3_eviction(
    tmp_path, monkeypatch,
):
    from tests.test_core_library_placement_identity_scope import _service

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    service = _service(tmp_path)
    try:
        book = tmp_path / "library" / "alice" / "book.epub"
        book.parent.mkdir(parents=True, exist_ok=True)
        raw = _write_epub(book, epub3=True)
        indexed = service.catalog_index_path(
            book, title="Verified Book", identity_id="alice",
            provider_id="local", item_id="book-a",
        )
        assert indexed["status"] == "indexed"
        artifact_id = "sha256:" + hashlib.sha256(raw).hexdigest()
        local, = service.library_entries(identity_id="alice", scan=False)
        recent, = service.recent_acquisitions(identity_id="alice")
        a = local.get("_exact_local_cover_evidence")
        b = recent.get("_exact_local_cover_evidence")
        assert a is not None and b is not None
        assert a.scope == b.scope
        assert a.scope.identity_id == "alice"
        assert a.scope.artifact_id == artifact_id
        assert a.scope.placement_id == recent["placement_id"]
        assert a.embedded.resource.cache_name == b.embedded.resource.cache_name
        image = epub_metadata.cover_cache_dir() / a.embedded.resource.cache_name
        image.unlink()
        library_fresh, = service.library_entries(identity_id="alice", scan=False)
        recent_fresh, = service.recent_acquisitions(identity_id="alice")
        assert library_fresh["_exact_local_cover_evidence"].scope == a.scope
        assert recent_fresh["_exact_local_cover_evidence"].scope == b.scope
        assert not image.exists()
        assert service.recent_acquisitions(identity_id="bob") == []
    finally:
        service.close()


def test_core_recent_only_uses_exact_artifact_metadata_after_scoped_path():
    code = (Path(__file__).resolve().parents[1] /
            "src/cifereca/core/library_projection.py").read_text(encoding="utf-8")
    start = code.index("def recent_acquisitions(")
    end = code.index("def local_book_path(", start)
    block = code[start:end]
    assert "verified_path = self.local_book_path(" in block
    assert 'artifact_metadata=artifact["metadata"]' in block
    assert '"_exact_local_cover_evidence"] = packet' in block
    assert block.index("verified_path = self.local_book_path(") < (
        block.index("packet = project_verified_local_cover_evidence(")
    )
