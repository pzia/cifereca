"""#3724/#3995: explicit OPDS language declaration belongs to the selected entry.

Synthetic XML fixture, not a claim of current Gutenberg server output.
"""
import pytest

from cifereca_gutenberg import GutenbergSession


def _entry(tags="", *, href="/ebooks/11.epub", image=""):
    return f"""<entry><id>https://www.gutenberg.org/ebooks/11</id>
      <title>Alice</title>{tags}{image}
      <link rel="http://opds-spec.org/acquisition"
            type="application/epub+zip" href="{href}"/>
    </entry>"""


def _parse(*entries):
    feed = ('<feed xmlns="http://www.w3.org/2005/Atom" '
            'xmlns:dc="http://purl.org/dc/terms/">' + "".join(entries) + "</feed>")
    session = GutenbergSession(
        credential_id="public", label="", credentials={},
        options={"contact": "tests@example.test"}, config={}, diagnostics=None,
    )
    try:
        rows = session._parse_feed(feed.encode("utf-8"),
                                   "https://www.gutenberg.org/ebooks/search.opds/")
        assert len(rows) == 1
        return rows[0]
    finally:
        session.close()


def test_complete_language_set_retains_raw_tokens_and_normalized_order():
    row = _parse(_entry("<dc:language>fre</dc:language><dc:language>eng</dc:language>"
                        "<dc:language>fra</dc:language>"))
    assert row.metadata["language"] == "fr"
    assert row.metadata["languages"] == ["fr", "en"]
    assert row.metadata["bibliography_evidence"]["languages"] == {
        "raw_tags": ["fre", "eng", "fra"],
        "source": "gutenberg.opds", "source_field": "dcterms.language",
        "scope": "OPDS", "confidence": "explicit_record_field",
        "freshness": "search_response",
    }
    assert row.downloadable and not row.owned


@pytest.mark.parametrize("tags", [
    "",
    "<dc:language>fr</dc:language><dc:language>???</dc:language>",
    "<dc:language>fr</dc:language><dc:language/>",
    "<dc:language><b>fr</b></dc:language>",
    '<dc:language xml:lang="en">fr</dc:language>',
    "<dc:language>fr</dc:language>" * 17,
])
def test_missing_or_incomplete_declaration_never_attests_subset(tags):
    row = _parse(_entry(tags))
    assert "languages" not in row.metadata
    assert "bibliography_evidence" not in row.metadata


def test_selected_edition_does_not_borrow_sibling_language():
    # Identical rank preserves feed order, irrespective of image preference.
    first = _entry()
    sibling = _entry("<dc:language>eng</dc:language>",
                     image='<link rel="http://opds-spec.org/image" href="/cover.jpg"/>')
    row = _parse(first, sibling)
    assert row.thumbnail_url == "https://www.gutenberg.org/cover.jpg"
    assert row.metadata["cover_source"] == "opds_other_edition"
    assert row.metadata["language"] is None
    assert "bibliography_evidence" not in row.metadata
