"""#1599: Constellations projects only explicitly attested public thumbnails.

No GTK, network, local file access, account lookup or acquisition rights.
"""
from types import SimpleNamespace as NS

import pytest

from cifereca.plugin_api import BookRef
from constellations_page import constellation_detail_presentation
from constellation_view_model import node_from_row


def detail(row):
    return constellation_detail_presentation(node_from_row(row))


@pytest.mark.parametrize("field", ("cover_url", "thumbnail_url"))
def test_direct_public_cover_is_visible_in_master_detail(field):
    url = "https://covers.example.org/images/edition-1.png"
    presentation = detail({
        "book_id": "edition-1", "title": "A book", field: url,
        "friendly_url": "https://catalogue.example.org/books/edition-1",
    })
    assert presentation.thumbnail_url == url
    assert len(presentation.links) == 1
    assert presentation.links[0].url == "https://catalogue.example.org/books/edition-1"
    assert presentation.actions == ()


@pytest.mark.parametrize("nested", ("metadata", "search_result"))
def test_nested_public_cover_is_used_when_direct_url_is_unsafe(nested):
    safe = "https://public.example.org/covers/edition-1.jpg"
    metadata = (
        {"cover_url": safe} if nested == "metadata"
        else {"search_result": {"thumbnail_url": safe}}
    )
    presentation = detail({
        "book_id": "edition-1", "title": "A book",
        "cover_url": "https://covers.example.org/covers/private.jpg?token=PRIVATE_CREDENTIAL",
        "metadata": metadata,
    })
    assert presentation.thumbnail_url == safe


@pytest.mark.parametrize("unsafe", (
    "file:///home/PRIVATE_USER/cover.jpg",
    "http://127.0.0.1/PRIVATE_USER.jpg",
    "https://127.1/PRIVATE_USER.jpg",
    "https://covers.local/private.jpg",
    "https://covers.example.org/cover.jpg?token=PRIVATE_TOKEN",
    "https://covers.example.org/download/book.epub",
    "https://covers.example.org/book%2Flicense%2Fprivate.jpg",
    "javascript:PRIVATE_CREDENTIAL",
))
def test_private_or_acquisition_cover_is_never_displayed(unsafe):
    presentation = detail({
        "book_id": "edition-1", "title": "A book",
        "cover_url": unsafe,
    })
    assert presentation.thumbnail_url is None
    assert presentation.actions == ()


def test_no_attested_cover_does_not_invent_thumbnail_or_ownership():
    presentation = detail({
        "book_id": "edition-1", "title": "A book",
        "metadata": {"availability": "owned"},
    })
    assert presentation.thumbnail_url is None
    assert presentation.actions == ()
    assert "possédé" not in presentation.badges


def test_same_title_editions_retain_distinct_attested_thumbnails():
    first = detail({
        "book_id": "edition-a", "title": "Same title",
        "cover_url": "https://covers.example.org/edition-a.jpg",
    })
    second = detail({
        "book_id": "edition-b", "title": "Same title",
        "cover_url": "https://covers.example.org/edition-b.jpg",
    })
    assert first.title == second.title
    assert first.thumbnail_url != second.thumbnail_url


def test_poisoned_mapping_cannot_crash_detail_or_leak_exception_text():
    class Poisoned(dict):
        def get(self, *_args):
            raise RuntimeError("Bearer PRIVATE_COVER_TOKEN")

    presentation = constellation_detail_presentation(NS(
        book=BookRef("edition-a", "A book", ()),
        sources=(), availability=(), url="", raw=Poisoned(),
    ))
    assert presentation.thumbnail_url is None
    assert "PRIVATE" not in str(presentation)
