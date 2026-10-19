"""#1917: malformed Gutenberg Atom XML must not be misreported as a next-link fault."""
import pytest
from cifereca_gutenberg import GutenbergSession


def test_broken_atom_feed_is_xml_error_before_pagination():
    with pytest.raises(Exception, match="XML invalide") as err:
        GutenbergSession._next_page_url(
            b"<feed><entry>broken",
            "https://www.gutenberg.org/ebooks/search.opds/?query=test",
        )
    assert "opds_next_link_unverified" not in str(err.value)
    assert "broken" not in str(err.value)


def test_well_formed_but_cross_origin_next_still_rejected():
    feed = (b'<feed xmlns="http://www.w3.org/2005/Atom">'
            b'<link rel="next" href="https://private.invalid/feed"/></feed>')
    with pytest.raises(Exception, match="opds_next_link_unverified"):
        GutenbergSession._next_page_url(
            feed, "https://www.gutenberg.org/ebooks/search.opds/?query=test"
        )
