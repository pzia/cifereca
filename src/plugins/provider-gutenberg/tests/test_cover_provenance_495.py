from cifereca_gutenberg import GutenbergSession


class Diagnostics:
    def add(self, *_args, **_kwargs):
        return None


def _session():
    return GutenbergSession(
        credential_id="public",
        label="",
        credentials={},
        options={"contact": "tests@example.test"},
        config={},
        diagnostics=Diagnostics(),
    )


def test_opds_thumbnail_publishes_catalog_cover_with_ebook_evidence():
    xml = b'''<feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <id>https://www.gutenberg.org/ebooks/123</id>
        <title>Example</title>
        <link rel="http://opds-spec.org/image/thumbnail" href="/cache/epub/123/pg123.cover.medium.jpg"/>
        <link rel="http://opds-spec.org/acquisition" type="application/epub+zip" href="/ebooks/123.epub.images"/>
      </entry>
    </feed>'''
    session = _session()
    try:
        row = session._parse_feed(xml, "https://www.gutenberg.org/ebooks/search.opds/")[0]
    finally:
        session.close()

    assert row.thumbnail_url == "https://www.gutenberg.org/cache/epub/123/pg123.cover.medium.jpg"
    assert row.metadata["catalog_cover"] == {
        "url": row.thumbnail_url,
        "source": "gutenberg.opds_thumbnail",
        "evidence_id": "gutenberg:123",
        "evidence_strength": 100,
    }


def test_entry_without_cover_does_not_invent_catalog_cover():
    xml = b'''<feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <id>https://www.gutenberg.org/ebooks/124</id>
        <title>No cover</title>
        <link rel="http://opds-spec.org/acquisition" type="application/epub+zip" href="/ebooks/124.epub"/>
      </entry>
    </feed>'''
    session = _session()
    try:
        row = session._parse_feed(xml, "https://www.gutenberg.org/ebooks/search.opds/")[0]
    finally:
        session.close()

    assert row.thumbnail_url is None
    assert "catalog_cover" not in row.metadata
