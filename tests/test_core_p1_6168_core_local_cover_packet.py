"""#6168: Core attaches an exact local EPUB proof, regardless of N3 bytes."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from cifereca import epub_metadata
from cifereca.core.covers import candidate
from cifereca.core.local_cover_evidence import (
    VerifiedLocalCoverEvidence, project_verified_local_cover_evidence,
)
from tests.test_epub_cover_contract import _write_epub


@pytest.fixture
def attested(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    book = tmp_path / "indexed.epub"
    raw = _write_epub(book, epub3=True)
    sha = hashlib.sha256(raw).hexdigest()
    metadata = epub_metadata.inspect_epub(book)
    assert metadata["epub_cover_status"] == "extracted"
    origin = candidate(
        "https://covers.public.example.org/a.png", tier="origin",
        source="catalog-entry", evidence_id="local:edition-a",
    )
    scope = dict(
        identity_id="alice", book_key="artifact:sha256:" + sha,
        artifact_id="sha256:" + sha, placement_id="placement:a",
        artifact_sha256=sha, artifact_metadata=metadata,
        verified_path=book, candidates=(origin,),
    )
    physical = epub_metadata.cover_cache_dir() / metadata["epub_cover"]["cache_name"]
    assert epub_metadata.valid_cached_cover_path(physical)
    return scope, origin, physical


def test_epub_evidence_survives_n3_miss_and_prioritizes_embedded(attested):
    kwargs, origin, physical = attested
    physical.unlink()
    facts = project_verified_local_cover_evidence(**kwargs)
    assert type(facts) is VerifiedLocalCoverEvidence
    assert facts.scope.identity_id == "alice"
    assert facts.scope.artifact_id == kwargs["artifact_id"]
    assert facts.scope.placement_id == kwargs["placement_id"]
    assert facts.embedded.candidate.tier == "embedded"
    assert facts.embedded.candidate.url == physical.as_uri()
    assert facts.embedded.resource.source_revision == 0
    assert facts.candidates == (origin,)
    assert not physical.exists()


def test_equal_artifact_on_another_profile_does_not_share_document_scope(attested):
    kwargs, _origin, _image = attested
    alice = project_verified_local_cover_evidence(**kwargs)
    bob = project_verified_local_cover_evidence(
        **{**kwargs, "identity_id": "bob", "placement_id": "placement:b"},
    )
    assert alice.embedded.candidate.url == bob.embedded.candidate.url
    assert alice.scope != bob.scope
    assert alice.embedded.resource.scope != bob.embedded.resource.scope


@pytest.mark.parametrize("changed", [
    {"identity_id": ""},
    {"book_key": ""},
    {"placement_id": ""},
    {"artifact_id": ""},
    {"artifact_sha256": "not-sha"},
    {"artifact_sha256": "a" * 64},
    {"verified_path": Path("relative.epub")},
    {"verified_path": "file:///tmp/other.epub"},
    {"artifact_metadata": []},
    {"candidates": [1]},
])
def test_unverified_inputs_cannot_mint_owner_packet(attested, changed):
    kwargs, _origin, _image = attested
    assert project_verified_local_cover_evidence(
        **{**kwargs, **changed},
    ) is None


def test_image_bytes_and_filename_are_not_documentary_links(attested):
    kwargs, origin, physical = attested
    physical.unlink()
    # A published origin still has its own N1 tier; losing physical
    # embedded bytes cannot change the upstream artifact->member proof.
    first = project_verified_local_cover_evidence(**kwargs)
    assert first is not None and first.embedded is not None
    assert origin in first.candidates
    assert first.scope.source_key == kwargs["book_key"]
    assert first.embedded.resource.cache_name == physical.name


def test_invalid_untrusted_candidate_or_embedded_tier_is_refused(attested):
    kwargs, origin, _image = attested
    embedded = candidate(
        origin.url, tier="embedded", source="unverified",
        evidence_id="fake",
    )
    assert project_verified_local_cover_evidence(
        **{**kwargs, "candidates": (embedded,)},
    ) is None
    assert project_verified_local_cover_evidence(
        **{**kwargs, "candidates": (object(),)},
    ) is None


def test_packet_only_created_after_verified_library_path():
    source = (
        Path(__file__).resolve().parents[1]
        / "src/cifereca/core/library_projection.py"
    ).read_text(encoding="utf-8")
    checked = source.index("scoped_local_path = self.local_book_path(")
    published = source.index("exact_cover_facts = project_verified_local_cover_evidence(")
    assert checked < published
    assert "if (scoped_local_path is not None and sha256" in source
    assert 'artifact_metadata=artifact["metadata"]' in source
    assert 'existing["_exact_local_cover_evidence"] = exact_cover_facts' in source
    assert 'existing.pop("_exact_local_cover_evidence", None)' in source


def test_private_packet_not_allowed_in_foreign_global_display():
    source = (
        Path(__file__).resolve().parents[1]
        / "src/cifereca/gui/library_global_projection.py"
    ).read_text(encoding="utf-8")
    # Foreign global projection is an explicit field allowlist, not a
    # shallow copy of owner DTOs with paths/credentials/cover proofs.
    assert "_FOREIGN_BOOK_FIELDS = frozenset(" in source
    allowed = source.split("_FOREIGN_BOOK_FIELDS = frozenset(", 1)[1].split("})", 1)[0]
    assert "_exact_local_cover_evidence" not in allowed
