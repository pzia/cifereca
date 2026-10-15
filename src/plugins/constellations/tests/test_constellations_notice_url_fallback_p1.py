"""#1599: an independently attested notice_url can rescue a rejected friendly link.

Source URLs are synthetic; no HTTP, GTK, ownership or acquisition.
"""
from __future__ import annotations

import pytest

from constellation_view_model import node_from_row
from constellations_page import constellation_detail_presentation


SAFE = "https://catalogue.example.org/books/edition-1"


@pytest.mark.parametrize("layer", ("row", "metadata", "search_result"))
def test_public_notice_url_fallback_from_each_declared_layer(layer):
    row = {
        "book_id": "edition-1",
        "title": "Public edition",
        "friendly_url": "https://catalogue.example.org/books%252fdownload%252fPRIVATE_TOKEN",
        "metadata": {},
    }
    if layer == "row":
        row["notice_url"] = SAFE
    elif layer == "metadata":
        row["metadata"]["notice_url"] = SAFE
    else:
        row["metadata"]["search_result"] = {"notice_url": SAFE}

    node = node_from_row(row)
    assert node.url == SAFE
    projected = constellation_detail_presentation(node)
    assert len(projected.links) == 1
    assert projected.links[0].uri == SAFE
    assert projected.title == "Public edition"


@pytest.mark.parametrize("unsafe", (
    "https://catalogue.example.org/books?token=PRIVATE_SECRET",
    "https://catalogue.example.org/books%2fdownload%2fPRIVATE_TOKEN",
    "https://catalogue.example.org/books%252flicense%252fPRIVATE_TOKEN",
    "http://127.0.0.1/PRIVATE_NOTICE",
    "file:///home/PRIVATE_USER/book.epub",
    "javascript:PRIVATE_TOKEN",
    "https://catalogue.example.org/book.lcpl",
    "https://catalogue.example.org/books%0dPRIVATE_TOKEN",
    "https://user:PRIVATE_PASSWORD@catalogue.example.org/books",
    {"url": SAFE},
    False,
))
def test_private_notice_url_never_crosses_detail_link_boundary(unsafe):
    node = node_from_row({
        "book_id": "edition-1", "title": "Public",
        "notice_url": unsafe,
        "metadata": {
            "notice_url": unsafe,
            "search_result": {"notice_url": unsafe},
        },
    })
    assert node.url == ""
    assert constellation_detail_presentation(node).links == ()


def test_public_friendly_link_keeps_priority_over_secondary_notice():
    node = node_from_row({
        "book_id": "edition-1", "title": "Title",
        "friendly_url": "https://catalogue.example.org/books/preferred",
        "notice_url": "https://catalogue.example.org/books/alternative",
    })
    assert node.url == "https://catalogue.example.org/books/preferred"


def test_notice_fallback_remains_bound_to_the_exact_edition():
    rows = [
        {"book_id": "edition-a", "title": "Same title",
         "notice_url": "https://catalogue.example.org/edition-a"},
        {"book_id": "edition-b", "title": "Same title",
         "notice_url": "https://catalogue.example.org/edition-b"},
    ]
    cards = [node_from_row(item) for item in rows]
    assert cards[0].key != cards[1].key
    assert [card.url for card in cards] == [
        "https://catalogue.example.org/edition-a",
        "https://catalogue.example.org/edition-b",
    ]
