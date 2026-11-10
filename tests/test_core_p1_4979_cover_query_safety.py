"""P1 #4979: shared cover URLs must not carry bearer or signed secrets.

No HTTP access, credentials or provider session involved.
"""
import pytest

from cifereca.core.covers import CoverCandidate, candidate, resolve_cover


@pytest.mark.parametrize("url", [
    "https://cdn.example/cover.jpg?token=PRIVATE",
    "https://cdn.example/cover.jpg?access_token=PRIVATE",
    "https://cdn.example/cover.jpg?X-Amz-Signature=PRIVATE",
    "https://cdn.example/cover.jpg?X-Amz-Credential=PRIVATE",
    "https://cdn.example/cover.jpg?api_key=PRIVATE",
    "https://cdn.example/cover.jpg?%74oken=PRIVATE",
    "https://cdn.example/cover.jpg#PRIVATE_SESSION",
])
def test_private_cover_query_is_never_admitted(url):
    assert candidate(url, tier="catalog", source="catalogue") is None
    assert resolve_cover([candidate("https://cdn.example/safe.jpg", tier="origin", source="origin"),
                          # Direct candidate construction cannot bypass resolver validation.
                          CoverCandidate(
                              url, "catalog", "catalogue")]).url == "https://cdn.example/safe.jpg"


def test_public_feedbooks_cover_query_remains_supported():
    public = "https://covers.feedbooks.net/item/123.jpg?size=medium&t=1717"
    selected = candidate(public, tier="catalog", source="bnf")
    assert selected is not None
    result = resolve_cover([selected])
    assert result.status == "selected"
    assert result.url == public


@pytest.mark.parametrize("url", [
    "https://cdn.example/cover.jpg?X-Goog-Signature=PRIVATE",
    "https://cdn.example/cover.jpg?X-Goog-Algorithm=GOOG4-RSA-SHA256",
    "https://cdn.example/cover.jpg?X-Goog-Credential=PRIVATE",
    "https://cdn.example/cover.jpg?Key-Pair-Id=PRIVATE",
    "https://cdn.example/cover.jpg?Policy=PRIVATE",
    "https://cdn.example/cover.jpg?cloudfront-policy=PRIVATE",
    "https://cdn.example/cover.jpg?size=medium;token=PRIVATE",
    "https://cdn.example/cover.jpg?%2574oken=PRIVATE",
    "https://cdn.example/cover.jpg?%2558-Goog-Signature=PRIVATE",
])
def test_cloud_signed_and_double_encoded_query_aliases_cannot_enter_cover(url):
    assert candidate(url, tier="catalog", source="catalogue") is None
    assert resolve_cover([
        CoverCandidate(url, "origin", "untrusted"),
    ]).status == "missing"


@pytest.mark.parametrize("tier", ["catalog", "origin"])
def test_nonembedded_cover_cannot_reference_a_local_file_even_when_manually_constructed(tier):
    uri = "file:///home/account-B/.cache/cifereca/private-cover.png"
    assert candidate(uri, tier=tier, source="foreign-catalogue") is None
    assert resolve_cover([
        CoverCandidate(uri, tier, "foreign-catalogue"),
    ]).status == "missing"


def test_exact_embedded_file_remains_possible_and_public_query_retained():
    local = candidate(
        "file:///home/reader/.cache/cifereca/cover.png",
        tier="embedded", source="epub", evidence_id="sha256:abc",
    )
    assert local is not None
    assert resolve_cover([local]).tier == "embedded"
    public = candidate(
        "https://covers.feedbooks.net/item/123.jpg?size=medium&t=1717",
        tier="catalog", source="public-catalogue",
    )
    assert public is not None
    assert resolve_cover([public]).url == public.url
