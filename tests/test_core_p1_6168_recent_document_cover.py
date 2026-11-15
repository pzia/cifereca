"""#6168: Récents and Bibliothèque use the SAME verified EPUB N2 source."""
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
def known(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    path = tmp_path / "book.epub"
    content = _write_epub(path, epub3=True)
    digest = hashlib.sha256(content).hexdigest()
    proof = project_verified_local_cover_evidence(
        identity_id="alice", book_key="artifact:sha256:" + digest,
        artifact_id="sha256:" + digest, placement_id="placement:a",
        artifact_sha256=digest, verified_path=path,
        artifact_metadata=epub_metadata.inspect_epub(path),
    )
    assert proof is not None and proof.embedded is not None
    row = {
        "placement_id": "placement:a", "artifact_id": "sha256:" + digest,
        "identity_id": "alice", "provider_id": "local",
        "title": "Exact", "author": "Writer",
        "thumbnail_url": "https://public.example.org/cover.png",
        "_exact_local_cover_evidence": proof, "acquired_at": 0,
    }
    return proof, row


def project(row):
    rows, mapped = project_recent_rows(
        [row], row_factory=Row,
        provider_display_name=lambda _provider: "Source",
    )
    assert mapped[rows[0].args[0]] is row
    return rows[0]


def test_recent_uses_attested_documentref_not_its_placement_display_key(known):
    proof, raw = known
    recent = project(raw)
    assert recent.args[0] == "placement:a"
    assert recent._cover_document_ref == proof.scope
    assert recent._cover_document_ref.source_key.startswith("artifact:sha256:")
    assert recent._cover_local_evidence is proof
    assert recent._public_row_content[-1] is proof


@pytest.mark.parametrize("change", [
    {"identity_id": "bob"}, {"artifact_id": "sha256:" + "b" * 64},
    {"placement_id": "placement:b"},
    {"_exact_local_cover_evidence": object()},
])
def test_recent_never_adopts_mismatched_or_untrusted_core_packet(known, change):
    _proof, raw = known
    recent = project({**raw, **change})
    assert not hasattr(recent, "_cover_document_ref")


def test_recent_detail_requires_same_current_owner_not_just_same_n3_image():
    detail = (
        Path(__file__).resolve().parents[1] /
        "src/cifereca/gui/recent_detail_controller.py"
    ).read_text(encoding="utf-8")
    assert 'facts.scope.identity_id == owner' in detail
    assert 'facts.scope.artifact_id == item.get("artifact_id")' in detail
    assert 'facts.scope.placement_id == item.get("placement_id")' in detail
    assert "publisher.register(bridge, facts)" in detail
    assert 'summary_kwargs["cover_document"] = cover_document' in detail


def test_core_recent_uses_verified_local_path_not_catalogue_epub_member():
    core = (
        Path(__file__).resolve().parents[1] /
        "src/cifereca/core/library_projection.py"
    ).read_text(encoding="utf-8")
    assert "recent_row[\"_exact_local_cover_evidence\"] = local_facts" in core
    assert "self.local_book_path(" in core
    assert 'artifact_metadata=artifact["metadata"]' in core
    assert "project_verified_local_cover_evidence(" in core
    assert "except (ProviderConfigurationError, OSError, ValueError)" in core
