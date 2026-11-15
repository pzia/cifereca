"""#6168 local Library/Reader rows carry only exact Core-attested cover refs."""
from __future__ import annotations

from dataclasses import replace
import hashlib
from pathlib import Path

import pytest

from cifereca import epub_metadata
from cifereca.core.local_cover_evidence import project_verified_local_cover_evidence
from cifereca.gui.library_rows import project_library_rows
from tests.test_epub_cover_contract import _write_epub


class Row:
    def __init__(self, *args):
        self.args = args


@pytest.fixture
def local(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    book = tmp_path / "book.epub"
    binary = _write_epub(book, epub3=True)
    sha = hashlib.sha256(binary).hexdigest()
    facts = project_verified_local_cover_evidence(
        identity_id="alice", book_key="artifact:sha256:" + sha,
        artifact_id="sha256:" + sha, placement_id="placement:alice",
        artifact_sha256=sha, artifact_metadata=epub_metadata.inspect_epub(book),
        verified_path=book,
    )
    assert facts is not None and facts.embedded is not None
    row = {
        "key": facts.scope.source_key, "local": True,
        "identity_id": "alice", "provider_id": "local",
        "artifact_id": facts.scope.artifact_id,
        "placement_id": facts.scope.placement_id,
        "title": "Same title", "author": "Same author",
        "metadata": {}, "thumbnail_url": None,
        "_exact_local_cover_evidence": facts,
    }
    return facts, row


def project(entry):
    rows, indexed = project_library_rows(
        (entry,), row_factory=Row,
        provider_display_name=lambda _: "Local",
        availability_label=lambda _: "Local",
        backup_status_label=lambda _: "",
        format_date=str,
    )
    return rows[0], indexed


def test_local_row_attaches_exact_epub_even_without_published_n3_uri(local):
    facts, entry = local
    row, index = project(entry)
    assert row._cover_document_ref is facts.scope
    assert row._cover_local_evidence is facts
    assert row._public_row_content[-1] is facts
    assert index[entry["key"]] is entry


def test_cross_owner_row_may_describe_book_but_cannot_import_private_scope(local):
    facts, entry = local
    cross = {
        **entry, "identity_id": "bob",
        "_library_view_owner": "bob",
    }
    row, _ = project(cross)
    assert not hasattr(row, "_cover_document_ref")
    other = {**entry, "artifact_id": "sha256:" + "b" * 64}
    row, _ = project(other)
    assert not hasattr(row, "_cover_local_evidence")


def test_foreign_global_read_cannot_replay_owner_local_packet(local):
    _facts, entry = local
    global_view = {
        **entry, "_library_view_owner": "bob",
    }
    row, _ = project(global_view)
    assert not hasattr(row, "_cover_document_ref")


def test_same_visible_url_but_changed_core_evidence_invalidates_row_signature(local):
    facts, entry = local
    earlier, _ = project(entry)
    later, _ = project({
        **entry, "_exact_local_cover_evidence": replace(
            facts, verified_path=facts.verified_path.with_name("moved.epub"),
        ),
    })
    assert earlier._public_row_content != later._public_row_content
    assert earlier._public_row_content[6] == later._public_row_content[6]


def test_reader_propagates_only_exact_library_artifact_and_placement():
    path = (
        Path(__file__).resolve().parents[1]
        / "src/cifereca/gui/reader_page_controller.py"
    )
    source = path.read_text(encoding="utf-8")
    assert 'source.get("_exact_local_cover_evidence")' in source
    assert "facts.scope.identity_id == identity_id" in source
    assert "facts.scope.artifact_id == item.artifact_id" in source
    assert "facts.scope.placement_id == item.placement_id" in source
    assert "row._cover_document_ref = facts.scope" in source
