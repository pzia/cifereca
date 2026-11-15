"""#6168: Récents shares the exact Core EPUB source, never an URL/media join."""
from __future__ import annotations

import hashlib

import pytest

from cifereca import epub_metadata
from cifereca.core.local_cover_evidence import VerifiedLocalCoverEvidence
from cifereca.gui.recent_rows import project_recent_rows
from tests.test_core_library_placement_identity_scope import _service
from tests.test_epub_cover_contract import _write_epub


ORIGIN = "https://public.example.org/edition-a-cover.png"


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    service = _service(tmp_path)
    source = tmp_path / "library" / "alice" / "exact.epub"
    source.parent.mkdir(parents=True, exist_ok=True)
    raw = _write_epub(source, epub3=True)
    sha = hashlib.sha256(raw).hexdigest()
    artifact_id = "sha256:" + sha
    meta = epub_metadata.inspect_epub(source)
    assert meta["epub_cover_status"] == "extracted"
    service.storage.artifact_put({
        "artifact_id": artifact_id, "sha256": sha,
        "size": len(raw), "media_format": "epub",
        "metadata": meta,
    })
    placement_id = service.storage.placement_put({
        "artifact_id": artifact_id, "path": str(source),
        "identity_id": "alice", "state": "present",
        "provider_id": "local", "source_item_id": "source:a",
    })
    service.storage.catalog_entry_put({
        "artifact_id": artifact_id, "placement_id": placement_id,
        "identity_id": "alice", "provider_id": "local",
        "item_id": "source:a", "title": "Exact book",
        "local_state": "local", "media_format": "epub",
        "metadata": {"cover_url": ORIGIN},
    })
    physical = epub_metadata.cover_cache_dir() / meta["epub_cover"]["cache_name"]
    try:
        yield service, source, placement_id, artifact_id, physical
    finally:
        service.close()


def test_recent_core_exact_epub_proof_survives_physical_n3_eviction(prepared):
    service, source, pid, artifact_id, physical = prepared
    physical.unlink()
    assert not physical.exists()
    rows = service.recent_acquisitions(identity_id="alice")
    assert len(rows) == 1
    item = rows[0]
    assert item["placement_id"] == pid
    assert item["artifact_id"] == artifact_id
    assert item["key"] == pid
    facts = item["_exact_local_cover_evidence"]
    assert type(facts) is VerifiedLocalCoverEvidence
    assert facts.scope.identity_id == "alice"
    assert facts.scope.source_key == "artifact:" + artifact_id
    assert facts.scope.placement_id == pid
    assert facts.scope.artifact_id == artifact_id
    assert facts.verified_path == source
    assert facts.embedded is not None
    assert facts.embedded.candidate.tier == "embedded"
    assert facts.embedded.candidate.url == physical.as_uri()
    assert item["thumbnail_url"] == ORIGIN  # legacy descriptive URL only
    assert facts.candidates[0].url == ORIGIN
    assert not physical.exists()  # no Epub inspection or N3 extraction


def test_library_recent_and_detail_reuse_one_attested_documentref(prepared):
    service, _, pid, artifact_id, _ = prepared
    library = service.library_entries(identity_id="alice", scan=False)
    recent = service.recent_acquisitions(identity_id="alice")
    assert len(library) == len(recent) == 1
    book, acquired = library[0], recent[0]
    assert book["key"] == "artifact:" + artifact_id
    assert acquired["key"] == pid  # row UI key is not a new document
    lib_proof = book["_exact_local_cover_evidence"]
    recent_proof = acquired["_exact_local_cover_evidence"]
    assert lib_proof.scope == recent_proof.scope
    assert lib_proof.scope.placement_id == pid
    assert lib_proof.scope.artifact_id == artifact_id
    assert lib_proof.scope.identity_id == "alice"

    from cifereca.gui.local_detail_cover import (
        exact_local_detail_cover, exact_recent_detail_cover,
    )
    assert exact_local_detail_cover(
        book, active_identity_id="alice",
    ) is lib_proof
    assert exact_recent_detail_cover(
        acquired, active_identity_id="alice",
    ) is recent_proof

    visual, indexed = project_recent_rows(recent, row_factory=Row)
    assert indexed[pid] is acquired
    assert visual[0]._cover_document_ref == lib_proof.scope
    assert visual[0].args[5] is None  # never downgrade to descriptive URL


def test_library_then_recent_uses_same_n2_and_warm_shared_n3(prepared):
    service, _, pid, _, physical = prepared
    assert physical.is_file()  # the embedded image was inspected once
    library = service.library_entries(identity_id="alice", scan=False)[0]
    recent = service.recent_acquisitions(identity_id="alice")[0]
    from cifereca.gui.cover_loader import CoverLoader
    from cifereca.gui.document_cover_service import DocumentCoverService
    from cifereca.gui.local_cover_adapter import LocalCoverEvidenceAdapter
    loader = CoverLoader(max_workers=2)
    cover_service = DocumentCoverService(loader)
    adapter = LocalCoverEvidenceAdapter()
    try:
        from_library = adapter.register(
            cover_service, library["_exact_local_cover_evidence"],
        )
        first = cover_service.get_cover(
            from_library, "detail", lambda _path: None,
        )
        from_recent = adapter.register(
            cover_service, recent["_exact_local_cover_evidence"],
        )
        second = cover_service.get_cover(
            from_recent, "detail", lambda _path: None,
        )
        assert from_library == from_recent
        assert from_library.placement_id == pid
        assert first.source_uri == second.source_uri == physical.resolve().as_uri()
        assert first.still_current  # same admissible packet, no N2 revocation
        stats = cover_service.stats()
        assert stats["n2_register_new"] == 1
        assert stats["n2_register_reused"] == 1
        assert stats["n2_hit"] >= 1
        n3 = loader.stats()
        assert n3["n3_epub_hit"] >= 2
        assert n3["n3_epub_miss"] == 0  # no new EPUB read/extraction
    finally:
        adapter.close()
        cover_service.close()
        loader.close()


def test_recent_never_exposes_foreign_placement_or_private_epub_packet(prepared):
    service, source, pid, _artifact, _physical = prepared
    assert service.recent_acquisitions(identity_id="bob") == []
    assert "_exact_local_cover_evidence" not in repr(
        service.recent_acquisitions(identity_id="bob"),
    )
    source.unlink()
    assert service.recent_acquisitions(identity_id="alice") == []


class Row:
    def __init__(self, *args):
        self.args = args


def test_recent_row_receives_same_exact_documentref_without_media_lookup(prepared):
    service, source, pid, _artifact, physical = prepared
    physical.unlink()
    core = service.recent_acquisitions(identity_id="alice")
    visual, indexed = project_recent_rows(
        core, row_factory=Row,
        provider_display_name=lambda provider: provider.capitalize(),
    )
    assert len(visual) == 1 and indexed[pid] is core[0]
    ref = visual[0]._cover_document_ref
    assert ref == core[0]["_exact_local_cover_evidence"].scope
    assert visual[0]._cover_local_evidence is core[0]["_exact_local_cover_evidence"]
    # The GTK cell must fetch from N2, never from this legacy URL.
    assert visual[0].args[5] is None
    assert visual[0]._public_row_content[-1] is visual[0]._cover_local_evidence
    assert not physical.exists()


def test_cross_owner_and_forged_documentref_cannot_reach_recent_row(prepared):
    service, _source, pid, artifact, _physical = prepared
    original = service.recent_acquisitions(identity_id="alice")[0]
    for changed in (
        {"identity_id": "bob"},
        {"placement_id": "other"},
        {"artifact_id": "sha256:" + "0" * 64},
        {"key": "unrelated"},
        {"_exact_local_cover_evidence": object()},
    ):
        row = {**original, **changed}
        display, _ = project_recent_rows((row,), row_factory=Row)
        assert not hasattr(display[0], "_cover_document_ref")
        assert not hasattr(display[0], "_cover_local_evidence")
        assert display[0].args[5] is None  # forbidden URL downgrade


def test_duplicate_recent_display_key_keeps_rows_but_revokes_book_join(prepared):
    service, _source, pid, _artifact, _physical = prepared
    first = service.recent_acquisitions(identity_id="alice")[0]
    second = {**first, "title": "Conflicting same placement ID"}
    visual, indexed = project_recent_rows(
        [first, second], row_factory=Row,
    )
    assert len(visual) == 2
    assert indexed[pid] is second
    assert not hasattr(visual[0], "_cover_document_ref")
    assert not hasattr(visual[1], "_cover_document_ref")
    assert [row.args[5] for row in visual] == [None, None]
    assert all(getattr(row, "_recent_ambiguous_key", False) for row in visual)


def test_rows_without_exact_metadata_remain_backward_compatible(prepared):
    service, _source, pid, _artifact, _physical = prepared
    core = service.recent_acquisitions(identity_id="alice")
    raw = {name: value for name, value in core[0].items()
           if name not in ("key", "_exact_local_cover_evidence")}
    visual, indexed = project_recent_rows([raw], row_factory=lambda *x: x)
    assert len(visual) == 1 and len(visual[0]) == 6
    assert visual[0][0] == pid
    assert indexed[pid] is raw
    assert visual[0][5] == ORIGIN  # no Core packet: legacy compatibility


def test_duplicate_recent_legacy_urls_are_hidden_even_without_core_packets():
    # A shared GTK key is not enough to identify which cover belongs to which
    # source. One stale row must not obtain an accidental image from its URL.
    inputs = [
        {"placement_id": "duplicate", "title": "A", "thumbnail_url": ORIGIN},
        {"placement_id": "duplicate", "title": "B",
         "thumbnail_url": "https://public.example.org/other-cover.jpg"},
    ]
    rows, objects = project_recent_rows(inputs, row_factory=lambda *x: x)
    assert len(rows) == 2 and objects["duplicate"] is inputs[-1]
    assert rows[0][5] is None and rows[1][5] is None


def test_rejected_packet_never_downgrades_in_headless_row_factory(prepared):
    service, _, _, _, _ = prepared
    original = service.recent_acquisitions(identity_id="alice")[0]
    forged = {**original, "identity_id": "bob"}
    rows, objects = project_recent_rows([forged], row_factory=lambda *x: x)
    assert rows[0][5] is None
    assert objects[forged["placement_id"]] is forged
