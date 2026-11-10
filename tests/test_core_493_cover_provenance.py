from __future__ import annotations

import ast

import pytest
from pathlib import Path

from cifereca.core.covers import CoverCandidate, candidate, resolve_cover


ROOT = Path(__file__).resolve().parents[1]
SERVICES = ROOT / 'src/cifereca/core/library_projection.py'
COVERS = ROOT / 'src/cifereca/core/covers.py'


def test_embedded_cover_always_beats_origin_and_catalog():
    result = resolve_cover([
        CoverCandidate('https://catalog.example/cover.jpg', 'catalog', 'public', 'catalog:1', 999),
        CoverCandidate('https://store.example/cover.jpg', 'origin', 'store', 'item:1', 999),
        CoverCandidate('file:///tmp/cover.png', 'embedded', 'epub', 'sha256:abc', 1),
    ])

    assert result.status == 'selected'
    assert result.url == 'file:///tmp/cover.png'
    assert result.tier == 'embedded'
    assert result.source == 'epub'
    assert result.evidence_id == 'sha256:abc'


def test_stronger_evidence_selects_one_origin_cover():
    result = resolve_cover([
        CoverCandidate('https://example.test/legacy.jpg', 'origin', 'catalog-entry', 'legacy', 60),
        CoverCandidate('https://example.test/loan.jpg', 'origin', 'loan', 'loan:1', 90),
    ])

    assert result.status == 'selected'
    assert result.url.endswith('/loan.jpg')
    assert result.source == 'loan'


def test_equal_strength_conflicting_origin_covers_are_ambiguous():
    result = resolve_cover([
        CoverCandidate('https://a.example/cover.jpg', 'origin', 'backup-a', 'item:1', 100),
        CoverCandidate('https://b.example/cover.jpg', 'origin', 'backup-b', 'item:1', 100),
    ])

    assert result.status == 'ambiguous'
    assert result.url == ''
    assert result.tier == 'origin'
    assert len(result.candidates) == 2


def test_duplicate_url_is_not_ambiguous_and_keeps_strongest_provenance():
    url = 'https://example.test/cover.jpg'
    result = resolve_cover([
        CoverCandidate(url, 'catalog', 'public', 'catalog:1', 100),
        CoverCandidate(url, 'origin', 'loan', 'loan:1', 90),
    ])

    assert result.status == 'selected'
    assert result.url == url
    assert result.tier == 'origin'
    assert result.source == 'loan'


def test_invalid_and_missing_candidates_do_not_invent_cover():
    assert candidate('javascript:alert(1)', tier='catalog', source='bad') is None
    assert candidate('/tmp/not-a-uri.png', tier='embedded', source='bad') is None
    result = resolve_cover([])
    assert result.status == 'missing'
    assert result.url == ''


def test_file_cache_uri_is_accepted_without_network():
    local = candidate('file:///home/user/.cache/cifereca/cover.png', tier='embedded', source='epub')
    assert local is not None
    assert resolve_cover([local]).url.startswith('file://')


def test_resolution_metadata_is_bounded_and_explainable():
    result = resolve_cover([
        CoverCandidate('https://example.test/cover.jpg', 'catalog', 'bnf', 'ark:123', 50),
    ])

    assert result.metadata() == {
        'status': 'selected',
        'url': 'https://example.test/cover.jpg',
        'tier': 'catalog',
        'source': 'bnf',
        'evidence_id': 'ark:123',
        'candidate_count': 1,
    }


def test_library_and_recent_projections_use_the_common_resolver():
    source = SERVICES.read_text(encoding='utf-8')
    assert source.count('resolve_cover(cover_candidates)') >= 2
    assert 'metadata["cover_resolution"] = cover.metadata()' in source
    assert 'combined_meta["cover_resolution"] = cover.metadata()' in source


def test_cover_resolver_is_pure_and_has_no_title_author_matching():
    source = COVERS.read_text(encoding='utf-8')
    tree = ast.parse(source)
    resolver = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'resolve_cover')
    identifiers = {
        node.id for node in ast.walk(resolver)
        if isinstance(node, ast.Name)
    }
    attributes = {
        node.attr for node in ast.walk(resolver)
        if isinstance(node, ast.Attribute)
    }
    assert 'httpx' not in source
    assert 'requests' not in source
    assert 'title' not in identifiers | attributes
    assert 'author' not in identifiers | attributes


@pytest.mark.parametrize("url", [
    "https://user:password@covers.example/cover.jpg",
    "https://user@covers.example/cover.jpg",
    "https://[malformed/cover.jpg",
    "https://covers.example:invalid/cover.jpg",
    "file://foreign-server/home/user/cover.png",
    "file:///home/user/cover.png?access_token=secret",
    "https://covers.example/cover.jpg\\r\\nX-Private: secret",
    "https://covers.example/" + "x" * 4096,
])
def test_cover_url_with_credentials_bad_authority_or_controls_is_rejected(url):
    assert candidate(url, tier="catalog", source="synthetic") is None
    assert resolve_cover([CoverCandidate(url, "catalog", "synthetic")]).status == "missing"


def test_invalid_high_strength_cover_does_not_hide_verified_independent_candidate():
    result = resolve_cover([
        CoverCandidate("https://user:secret@covers.example/cover.jpg",
                       "embedded", "untrusted", evidence_strength=999),
        CoverCandidate("https://covers.example/public.jpg",
                       "catalog", "verified", evidence_strength=10),
    ])
    assert result.status == "selected"
    assert result.url == "https://covers.example/public.jpg"
    assert "secret" not in repr(result)


class ExplosiveProvenance:
    def __str__(self):
        raise AssertionError("untrusted provenance must never be coerced")

    def __repr__(self):
        raise AssertionError("private provider state must not leak")


@pytest.mark.parametrize("broken", [
    None,
    {},
    object(),
    CoverCandidate("https://bad.example/cover.jpg", [], "provider"),
    CoverCandidate("https://bad.example/cover.jpg", "catalog",
                   ExplosiveProvenance()),
    CoverCandidate("https://bad.example/cover.jpg", "catalog", "provider",
                   evidence_id=ExplosiveProvenance()),
    CoverCandidate("https://bad.example/cover.jpg", "catalog", "provider",
                   evidence_strength=ExplosiveProvenance()),
    CoverCandidate("https://bad.example/cover.jpg", "catalog", "provider",
                   evidence_strength="9999999999"),
    CoverCandidate("https://bad.example/cover.jpg", "catalog", "provider",
                   evidence_strength=True),
    CoverCandidate("https://bad.example/cover.jpg", "catalog", "provider",
                   evidence_strength=1001),
    CoverCandidate("https://bad.example/cover.jpg", "catalog",
                   "provider\nsecret"),
    CoverCandidate("https://bad.example/cover.jpg", "catalog", "provider",
                   evidence_id="x" * 513),
])
def test_malformed_cover_candidate_never_blocks_independent_valid_cover(broken):
    good = CoverCandidate(
        "https://covers.example.org/valid.jpg",
        "catalog", "attested", "source:exact", 80,
    )
    result = resolve_cover([broken, good])
    assert result.status == "selected"
    assert result.url == good.url
    assert result.metadata()["source"] == "attested"


def test_malformed_source_and_strength_are_rejected_at_constructor_boundary():
    assert candidate(
        "https://covers.example.org/book.jpg",
        tier="catalog", source=ExplosiveProvenance(),
    ) is None
    assert candidate(
        "https://covers.example.org/book.jpg",
        tier="catalog", source="catalogue",
        evidence_strength="100",
    ) is None
    assert candidate(
        "https://covers.example.org/book.jpg",
        tier="catalog", source="catalogue",
        evidence_strength=0,
    ) is not None
    assert candidate(
        "https://covers.example.org/book.jpg",
        tier="catalog", source="catalogue",
        evidence_strength=1000,
    ) is not None


def test_two_attested_equal_covers_remain_ambiguous_after_bad_candidates():
    result = resolve_cover([
        CoverCandidate("https://covers.example/a.png", "origin", "source-a", "edition:a", 90),
        CoverCandidate("https://covers.example/b.png", "origin", "source-b", "edition:b", 90),
        CoverCandidate("https://bad.example/c.png", "embedded", "untrusted",
                       evidence_strength=float("nan")),
    ])
    assert result.status == "ambiguous"
    assert not result.url
    assert len(result.candidates) == 2
