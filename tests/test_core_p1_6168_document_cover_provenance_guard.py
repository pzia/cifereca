"""#6168: never let a physical N3 cache hit forge an N1/N2 EPUB choice."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from cifereca import epub_metadata
from cifereca.core.book_cover_projection import CoverDocumentScope
from cifereca.core.covers import CoverCandidate, candidate
from cifereca.core.epub_cover_source import (
    AttestedEmbeddedCover, project_exact_embedded_cover,
)
from cifereca.gui.document_cover_service import CoverSourceSnapshot


@pytest.fixture
def exact(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    sha = "a" * 64
    scope = CoverDocumentScope(
        kind="placement", identity_id="owner",
        source_key="artifact:sha256:" + sha,
        artifact_id="sha256:" + sha, placement_id="p:exact",
    )
    metadata = {
        "epub_metadata_schema": epub_metadata.SCHEMA,
        "epub_status": "ok",
        "epub_cover_status": "extracted",
        "epub_cover": {
            "source": "epub", "member": "OPS/images/cover.png",
            "cache_name": "b" * 64 + ".png",
        },
    }
    embedded = project_exact_embedded_cover(
        scope, revision=3, artifact_sha256=sha,
        artifact_metadata=metadata,
    )
    assert embedded is not None
    origin = candidate(
        "https://public.example.org/cover.png", tier="origin",
        source="catalogue", evidence_id="edition:a",
    )
    file_path = tmp_path / "verified.epub"
    assert not (epub_metadata.cover_cache_dir() /
                embedded.resource.cache_name).exists()
    return scope, embedded, origin, file_path


def snapshot(exact, *, candidates=None, embedded=None):
    scope, genuine, origin, path = exact
    return CoverSourceSnapshot(
        scope=scope, revision=3,
        candidates=(origin,) if candidates is None else candidates,
        embedded=genuine if embedded is None else embedded,
        verified_epub_path=path,
    )


def test_approved_epub_proof_does_not_require_physical_image(exact):
    snap = snapshot(exact)
    assert snap.embedded.candidate.tier == "embedded"
    assert snap.candidates[0].tier == "origin"
    assert snap.embedded.candidate.url.endswith("b" * 64 + ".png")


@pytest.mark.parametrize("tier", ["embedded", "invalid"])
def test_catalogue_cannot_claim_epub_tier_in_candidate_inventory(exact, tier):
    scope, _embedded, origin, _path = exact
    forged = CoverCandidate(
        url=origin.url, tier=tier, source=origin.source,
        evidence_id=origin.evidence_id,
    )
    with pytest.raises(ValueError):
        snapshot(exact, candidates=(forged,))


def test_even_exact_epub_url_cannot_be_public_inventory_without_proof(exact):
    scope, real, origin, path = exact
    forged = candidate(
        real.candidate.url, tier="embedded",
        source="epub", evidence_id=scope.artifact_id,
    )
    with pytest.raises(ValueError):
        CoverSourceSnapshot(
            scope=scope, revision=3,
            candidates=(forged,), embedded=None,
        )


@pytest.mark.parametrize("change", [
    {"url": "https://public.example.org/fake.png"},
    {"url": "file:///tmp/unrelated/" + "b" * 64 + ".png"},
    {"url": "file:///tmp/other/" + "c" * 64 + ".png"},
    {"source": "catalogue"},
    {"evidence_id": "sha256:" + "c" * 64},
    {"tier": "origin"},
])
def test_embedded_evidence_must_match_exact_scoped_n3_content_key(exact, change):
    scope, real, origin, path = exact
    mutated = replace(real, candidate=replace(real.candidate, **change))
    with pytest.raises(ValueError):
        snapshot(exact, embedded=mutated)


def test_same_physical_image_does_not_authorize_different_placement(exact):
    scope, real, origin, path = exact
    other = replace(scope, identity_id="other-owner", placement_id="p:foreign")
    with pytest.raises(ValueError):
        CoverSourceSnapshot(
            scope=other, revision=3,
            embedded=real, verified_epub_path=path,
        )


def test_unrelated_warm_url_never_changes_epub_selection_candidate(exact):
    scope, real, origin, path = exact
    snap = snapshot(exact)
    assert snap.embedded.candidate.url != snap.candidates[0].url
    assert snap.embedded.candidate.tier == "embedded"
    assert snap.candidates[0].tier == "origin"
