from __future__ import annotations

import ast
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
