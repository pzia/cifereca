"""P1 #6168: N2 book->EPUB source is independent from N3 image bytes."""
from __future__ import annotations

from dataclasses import replace
import hashlib

import pytest

from cifereca import epub_metadata
from cifereca.core.book_cover_cache import CoverDecisionCache
from cifereca.core.book_cover_projection import (
    CoverDocumentScope, resolve_book_cover,
)
from cifereca.core.covers import candidate
from cifereca.core.epub_cover_materialization import EpubCoverPhysicalCache
from cifereca.core.epub_cover_source import project_exact_embedded_cover
from tests.test_epub_cover_contract import _write_epub


@pytest.fixture
def attested(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    book = tmp_path / "exact.epub"
    content = _write_epub(book, epub3=True)
    sha = hashlib.sha256(content).hexdigest()
    metadata = epub_metadata.inspect_epub(book)
    assert metadata["epub_cover_status"] == "extracted"
    scope = CoverDocumentScope(
        kind="placement", identity_id="alice",
        source_key="artifact:sha256:" + sha,
        artifact_id="sha256:" + sha, placement_id="placement-a",
    )
    physical = epub_metadata.cover_cache_dir() / metadata["epub_cover"]["cache_name"]
    assert epub_metadata.valid_cached_cover_path(physical)
    return book, sha, scope, metadata, physical


def _source(bundle, *, revision=5, scope=None, metadata=None):
    book, sha, actual_scope, actual_meta, _physical = bundle
    result = project_exact_embedded_cover(
        scope or actual_scope,
        revision=revision, artifact_sha256=sha,
        artifact_metadata=actual_meta if metadata is None else metadata,
    )
    return result


def test_epub_wins_n1_when_n3_image_missing_but_catalog_url_is_hot(attested):
    book, _sha, scope, _meta, physical = attested
    local = _source(attested)
    assert local is not None
    assert local.candidate.tier == "embedded"
    physical.unlink()  # N3 miss does not delete or downgrade N1.
    assert not physical.exists()

    origin = candidate(
        "https://public.example.org/book.jpg", tier="origin",
        source="source-exact", evidence_id="publication-exact",
    )
    catalog = candidate(
        "https://public.example.org/other.jpg", tier="catalog",
        source="catalogue-exact", evidence_id="notice-exact",
    )
    decision = resolve_book_cover(
        scope, [catalog, origin, local.candidate],
        revision=5, snapshot_complete=True,
    )
    assert (decision.status, decision.tier) == ("selected", "embedded")
    assert decision.uri == physical.as_uri()
    assert decision.uri != origin.url

    cache = CoverDecisionCache()
    token = cache.begin(scope, revision=5)
    assert cache.publish_resolved(token, decision)
    assert cache.peek(scope, revision=5) == decision

    media = EpubCoverPhysicalCache()
    try:
        result = media.request(local.resource, source_path=book).result(timeout=10)
        assert result == physical.as_uri()
        assert epub_metadata.valid_cached_cover_path(physical)
        # N3 rehydration neither clears N2 nor reselects an URL.
        assert cache.peek(scope, revision=5) == decision
        assert cache.selected_token(scope, revision=5, uri=origin.url) is None
        assert cache.selected_token(scope, revision=5, uri=physical.as_uri()) == token
    finally:
        media.close()


def test_epub_source_n1_does_not_need_cached_image_or_epub_open(attested, monkeypatch):
    book, _sha, _scope, _meta, physical = attested
    physical.unlink()
    book.unlink()
    def fail(*args, **kwargs):
        raise AssertionError("N1 is not allowed to open EPUB or N3 image")
    monkeypatch.setattr(epub_metadata, "valid_cached_cover_path", fail)
    local = _source(attested)
    assert local is not None
    assert local.candidate.url == physical.as_uri()


@pytest.mark.parametrize("mutate", [
    {"epub_metadata_schema": -1},
    {"epub_status": "unreadable_metadata"},
    {"epub_cover_status": "no_usable_image"},
    {"epub_cover": {"source": "catalog", "member": "OPS/images/cover.png"}},
    {"epub_cover": {"source": "epub", "member": "../other.png", "cache_name": "a" * 64 + ".png"}},
    {"epub_cover": {}},
])
def test_unattested_epub_evidence_never_outvotes_catalogue(attested, mutate):
    _book, sha, scope, base, _physical = attested
    modified = {**base, **mutate}
    assert project_exact_embedded_cover(
        scope, revision=5, artifact_sha256=sha,
        artifact_metadata=modified,
    ) is None
    public = candidate(
        "https://public.example.org/cover.png", tier="catalog",
        source="known-catalogue", evidence_id="notice-exact",
    )
    outcome = resolve_book_cover(scope, [public], revision=5, snapshot_complete=True)
    assert (outcome.status, outcome.tier, outcome.uri) == (
        "selected", "catalog", public.url,
    )


def test_mismatched_artifact_digest_fails_closed(attested):
    _book, _sha, scope, _metadata, _physical = attested
    forged = project_exact_embedded_cover(
        scope, revision=5, artifact_sha256="a" * 64,
        artifact_metadata=attested[3],
    )
    assert forged is None


def test_same_physical_image_never_joins_two_document_scopes(attested):
    _book, sha, first_scope, _metadata, _physical = attested
    second_scope = replace(
        first_scope, identity_id="bob", placement_id="placement-b",
    )
    a = _source(attested, scope=first_scope)
    b = _source(attested, scope=second_scope)
    assert a is not None and b is not None
    assert a.candidate.url == b.candidate.url  # N3 can deduplicate same bytes.
    assert a.resource.scope != b.resource.scope
    cache = CoverDecisionCache()
    first = resolve_book_cover(first_scope, [a.candidate], revision=5, snapshot_complete=True)
    assert cache.publish_resolved(cache.begin(first_scope, revision=5), first)
    assert cache.peek(second_scope, revision=5) is None
    assert _source(attested, revision=5, scope=second_scope).resource.scope.identity_id == "bob"


def test_malformed_source_does_not_prove_local_placement(attested):
    _book, sha, scope, metadata, _physical = attested
    invalid = replace(scope, kind="catalogue", provider_id="other-provider")
    assert project_exact_embedded_cover(
        invalid, revision=5, artifact_sha256=sha, artifact_metadata=metadata,
    ) is None
    assert project_exact_embedded_cover(
        scope, revision=True, artifact_sha256=sha, artifact_metadata=metadata,
    ) is None
