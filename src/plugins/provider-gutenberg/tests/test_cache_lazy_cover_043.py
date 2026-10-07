from collections import OrderedDict
from cifereca_gutenberg import GutenbergSession

def test_page_url_cache_records_direct_jump():
    session=GutenbergSession.__new__(GutenbergSession); session.opds_search_url='https://www.gutenberg.org/ebooks/search.opds/'; session._page_urls=OrderedDict(); session._page_cache_keys=2
    rows=session._page_cache('foo'); session._remember_page(rows,2,'https://www.gutenberg.org/p3'); assert rows[2].endswith('/p3')

def test_detail_cover_prefers_thumbnail():
    xml=b'<feed xmlns="http://www.w3.org/2005/Atom"><link rel="http://opds-spec.org/image" href="cover.jpg"/><link rel="http://opds-spec.org/image/thumbnail" href="thumb.jpg"/></feed>'
    assert GutenbergSession._detail_cover(xml,'https://www.gutenberg.org/ebooks/1.opds').endswith('/ebooks/thumb.jpg')
