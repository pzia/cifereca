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
