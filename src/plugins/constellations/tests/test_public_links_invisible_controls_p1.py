"""#1599: invisible and bidi control characters are not public link evidence.

URLs must remain safe after up to four downstream unquote() passes.
All tests use synthetic bibliographic notices; never fetch any URLs.
"""
from __future__ import annotations

import pytest

from constellation_view_model import node_from_row, public_url
from constellations_page import constellation_detail_presentation


@pytest.mark.parametrize("unsafe", (
    "https://catalogue.example.org/book/\u202eevil",
    "https://catalogue.example.org/book/\u2066private",
    "https://catalogue.example.org/book/\u200bhidden",
    "https://catalogue.example.org/book/\u00adsoft-hyphen",
    "https://catalogue.example.org/book/\ufeffbom",
    "https://catalogue.example.org/book/\u0085c1",
    "https://catalogue.example.org/book/\u2028line-separator",
    "https://catalogue.example.org/book/%E2%80%AEevil",
    "https://catalogue.example.org/book/%E2%81%A6private",
    "https://catalogue.example.org/book/%E2%80%8Bhidden",
    "https://catalogue.example.org/book/%C2%ADhidden",
    "https://catalogue.example.org/book/%25E2%2580%25AEevil",
    "https://catalogue.example.org/book/%2525E2%252580%2525AEevil",
    "https://catalogue.example.org/book/%C2%85secret",
    "https://catalogue.example.org/book/%E2%80%A8private",
))
def test_unsafe_unicode_cannot_reach_notice_or_thumbnail(unsafe):
    assert public_url(unsafe) == ""
    node = node_from_row({
        "book_id": "edition-a", "title": "A title", "friendly_url": unsafe,
        "cover_url": unsafe,
    })
    assert node.url == ""
    detail = constellation_detail_presentation(node)
    assert detail.links == ()
    assert detail.thumbnail_url is None


@pytest.mark.parametrize("safe", (
    "https://catalogue.example.org/books/%C3%89dition",
    "https://catalogue.example.org/books/%E2%82%AC",
    "https://catalogue.example.org/books/%20space",
    "https://catalogue.example.org/books/m%C3%A9moire",
    "https://catalogue.example.org/books/%25unescaped",
    "https://catalogue.example.org/books/édition",
))
def test_normal_bibliographic_unicode_or_percent_encoding_remains_valid(safe):
    assert public_url(safe) == safe
    node = node_from_row({
        "book_id": "edition-b", "title": "B", "friendly_url": safe,
        "thumbnail_url": safe,
    })
    detail = constellation_detail_presentation(node)
    assert detail.links[0].url == safe
    assert detail.thumbnail_url == safe
