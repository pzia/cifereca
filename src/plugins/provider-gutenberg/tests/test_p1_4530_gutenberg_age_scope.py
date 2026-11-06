"""#4530 — Gutenberg OPDS bookshelves and age-like subjects are not age evidence.

The fixtures are synthetic; no live Gutenberg requests, rights or downloads.
"""
from __future__ import annotations

import pytest

from cifereca_gutenberg import GutenbergSession

_CANONICAL_AGE = {
    "recommended_age", "youth_age_ranges", "attested_age_ranges",
    "attested_age_status", "public_age_range_label",
}


def _session(prefer_images):
    return GutenbergSession(
        credential_id="__public__", label="Project Gutenberg",
        credentials={}, options={"contact": "tests@example.test",
                                 "prefer_images": prefer_images},
        config={}, diagnostics=None,
    )


def _feed():
    # Two EPUB renditions share a Gutenberg work ID. A second, same-titled
    # Gutenberg work must never inherit that first work's subjects.
    return """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>https://www.gutenberg.org/ebooks/111</id>
    <title>Same title</title>
    <category scheme="http://www.gutenberg.org/2009/pgterms/LCSH"
              term="Juvenile fiction -- From 8 years"/>
    <category scheme="http://www.gutenberg.org/2009/pgterms/Bookshelf"
              term="Children's Bookshelf"/>
    <link rel="http://opds-spec.org/acquisition"
          type="application/epub+zip"
          href="/ebooks/111.epub.images"/>
  </entry>
  <entry>
    <id>https://www.gutenberg.org/ebooks/111</id>
    <title>Same title</title>
    <category scheme="http://www.gutenberg.org/2009/pgterms/LCSH"
              term="Young adult -- 13-99"/>
    <link rel="http://opds-spec.org/acquisition"
          type="application/epub+zip"
          href="/ebooks/111.epub.noimages"/>
  </entry>
  <entry>
    <id>https://www.gutenberg.org/ebooks/222</id>
    <title>Same title</title>
    <category scheme="http://www.gutenberg.org/2009/pgterms/LCSH"
              term="Adult fiction"/>
    <link rel="http://opds-spec.org/acquisition"
          type="application/epub+zip"
          href="/ebooks/222.epub.images"/>
  </entry>
</feed>""".encode("utf-8")


@pytest.mark.parametrize("prefer_images, expected_subject", [
    (True, "Juvenile fiction -- From 8 years"),
    (False, "Young adult -- 13-99"),
])
def test_bookshelves_and_rendering_choice_never_prove_age(
    prefer_images, expected_subject,
):
    session = _session(prefer_images)
    try:
        books = session._parse_feed(
            _feed(), "https://www.gutenberg.org/ebooks/search.opds/",
        )
    finally:
        session.close()
    assert len(books) == 2
    assert [book.record_id for book in books] == ["111", "222"]
    assert books[0].title == books[1].title == "Same title"
    assert books[0].metadata["subjects"] == [expected_subject]
    assert books[1].metadata["subjects"] == ["Adult fiction"]
    if prefer_images:
        assert books[0].metadata["bookshelves"] == ["Children's Bookshelf"]
    else:
        assert books[0].metadata["bookshelves"] == []
    assert all(not (_CANONICAL_AGE & book.metadata.keys()) for book in books)
    assert all(book.owned is False and book.downloadable is True
               and book.format == "EPUB" for book in books)


def test_age_looking_atom_elements_do_not_override_selected_rendering():
    # Synthetic extra XML is not an attested Gutenberg edition-level field.
    feed = _feed().replace(
        b"<title>Same title</title>",
        b"<title>Same title</title><audience>8-12</audience>",
    )
    session = _session(True)
    try:
        books = session._parse_feed(
            feed, "https://www.gutenberg.org/ebooks/search.opds/",
        )
    finally:
        session.close()
    assert [book.record_id for book in books] == ["111", "222"]
    assert all(not (_CANONICAL_AGE & book.metadata.keys()) for book in books)
