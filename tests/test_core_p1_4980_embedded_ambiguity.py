"""Resolver-level evidence only; no cross-placement association is minted."""
from itertools import permutations

import pytest

from cifereca.core.covers import CoverCandidate, resolve_cover


@pytest.mark.parametrize("order", list(permutations(range(3))))
def test_conflicting_embedded_covers_do_not_promote_catalogue(order):
    candidates = (
        CoverCandidate("file:///tmp/fixture-a.png", "embedded", "epub", "artifact:a", 100),
        CoverCandidate("file:///tmp/fixture-b.png", "embedded", "epub", "artifact:b", 100),
        CoverCandidate("https://catalog.example/cover.png", "catalog", "fixture", "catalog:1", 999),
    )
    result = resolve_cover([candidates[i] for i in order])
    assert result.status == "ambiguous"
    assert not result.url
    assert result.tier == "embedded"
    assert {item.url for item in result.candidates} == {candidates[0].url, candidates[1].url}
