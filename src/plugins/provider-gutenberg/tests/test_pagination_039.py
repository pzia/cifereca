import pytest
from cifereca.plugin_api import ProviderError
from cifereca_gutenberg import GutenbergSession


def test_next_page_uses_atom_rel_next_and_same_host():
    xml=b'''<feed xmlns="http://www.w3.org/2005/Atom"><link rel="next" href="?query=foo&amp;start_index=26"/></feed>'''
    url=GutenbergSession._next_page_url(xml,'https://www.gutenberg.org/ebooks/search.opds/?query=foo')
    assert url.startswith('https://www.gutenberg.org/ebooks/search.opds/')
    assert 'start_index=26' in url


def test_next_page_rejects_cross_host():
    xml=b'''<feed xmlns="http://www.w3.org/2005/Atom"><link rel="next" href="https://evil.example/next"/></feed>'''
    with pytest.raises(ProviderError, match="opds_next_link_unverified"):
        GutenbergSession._next_page_url(xml,'https://www.gutenberg.org/ebooks/search.opds/')
