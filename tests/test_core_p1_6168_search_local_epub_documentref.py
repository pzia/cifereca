"""#6168: Search-local list reuses only an already-attested Library N1 packet.

The actual GTK-free Search presenter is replayed through its existing AST
test host. No supplier I/O, EPUB scan, rights transfer or URL->book join.
"""
from __future__ import annotations

from dataclasses import replace
import hashlib

import pytest

from cifereca import epub_metadata
from cifereca.core.local_cover_evidence import project_verified_local_cover_evidence
from tests.test_search_results_view import _host
from tests.test_epub_cover_contract import _write_epub


@pytest.fixture
def search_book(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    book = tmp_path / "exact.epub"
    binary = _write_epub(book, epub3=True)
    sha = hashlib.sha256(binary).hexdigest()
    facts = project_verified_local_cover_evidence(
        identity_id="alice", book_key="artifact:sha256:" + sha,
        artifact_id="sha256:" + sha, placement_id="placement:alice",
        artifact_sha256=sha, verified_path=book,
        artifact_metadata=epub_metadata.inspect_epub(book),
    )
    assert facts is not None and facts.embedded is not None
    row = {
        "key": facts.scope.source_key,
        "local": True, "identity_id": "alice", "provider_id": "local",
        "artifact_id": facts.scope.artifact_id,
        "placement_id": facts.scope.placement_id,
        "title": "Exact Edition", "author": "Author",
        "thumbnail_url": "https://public.example.org/origin.png",
        "_exact_local_cover_evidence": facts,
    }
    return row, facts


def render(row, *, active="alice"):
    host = _host()
    host.service.current_identity.identity_id = active
    host._search_local_objects = {row["key"]: row}
    assert host._render_search_results() == 1
    actual, = host.search_page.show_rows.call_args.args[0]
    return actual


def test_search_local_exact_epub_survives_n3_eviction_and_keeps_original_url_tail(search_book):
    row, facts = search_book
    image = epub_metadata.cover_cache_dir() / facts.embedded.resource.cache_name
    image.unlink()  # Physical bytes do not determine documentary association.
    result = render(row)
    assert result._cover_document_ref == facts.scope
    assert result._cover_local_evidence is facts
    assert result._public_row_content[-2] is facts
    assert result._public_row_content[0] == "search"
    assert result._public_row_content[-1] is None  # headless Row has no URL slot
    assert not image.exists()


@pytest.mark.parametrize("mutation", [
    {"identity_id": "bob"},
    {"artifact_id": "sha256:" + "b" * 64},
    {"placement_id": "placement:other"},
    {"key": "unqualified:notice"},
    {"_exact_local_cover_evidence": object()},
])
def test_search_local_refuses_cross_scope_or_untrusted_packets(search_book, mutation):
    row, _facts = search_book
    mutated = {**row, **mutation}
    result = render(mutated)
    assert not hasattr(result, "_cover_document_ref")
    assert not hasattr(result, "_cover_local_evidence")
    # The public Search-C epoch and query-context slots were added by P0
    # #6472/#6473; they remain empty on an unqualified local EPUB packet.
    # The documentary source is still at [-2], URL at [-1].
    assert len(result._public_row_content) == 12
    assert result._public_row_content[-4:-2] == (None, None)
    assert result._public_row_content[-2:] == (None, None)


def test_active_profile_bob_never_adopts_alice_packet(search_book):
    row, _facts = search_book
    result = render(row, active="bob")
    assert not hasattr(result, "_cover_document_ref")


def test_different_core_packet_rebinds_same_visible_row_even_if_thumbnail_unchanged(
    search_book,
):
    row, facts = search_book
    first = render(row)
    updated = replace(
        facts, verified_path=facts.verified_path.with_name("moved.epub"),
    )
    second = render({**row, "_exact_local_cover_evidence": updated})
    assert first._public_row_content != second._public_row_content
    assert first._public_row_content[-2] is facts
    assert second._public_row_content[-2] is updated
    assert first._public_row_content[-1] == second._public_row_content[-1]


def test_search_never_registers_all_offscreen_sources_to_n2():
    from pathlib import Path
    source = (
        Path(__file__).resolve().parents[1] /
        "src/cifereca/gui/search_results.py"
    ).read_text(encoding="utf-8")
    start = source.index("def _render_search_results(")
    block = source[start:source.index("def ", start + 4)]
    assert "view_row._cover_document_ref = proof.scope" in block
    assert "document_covers.register(" not in block
    assert "local_cover_adapter.register(" not in block
    assert "from cifereca.core.local_cover_evidence import VerifiedLocalCoverEvidence" in block
