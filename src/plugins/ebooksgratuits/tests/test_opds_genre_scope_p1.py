"""#1917: OPDS category never masquerades as an attested genre."""
from types import SimpleNamespace
import xml.etree.ElementTree as ET

import pytest

import cifereca_ebooksgratuits as eg


@pytest.fixture
def offline(monkeypatch):
    feed = ET.fromstring('<feed xmlns="http://www.w3.org/2005/Atom" />')
    url = eg.OPDS_FEED
    calls = []
    def root(_session, _url):
        calls.append("root")
        return feed, url
    def search(_session, _root, _base, _query):
        calls.append("search")
        return url
    def page(_session, _url, _field, _needle):
        calls.append("page")
        return feed, url
    rows = [
        SimpleNamespace(title="One", author="Author", metadata={
            "categories": ["Fantasy"], "genres": [],
        }),
        SimpleNamespace(title="Two", author="Author", metadata={
            "categories": ["Novel"], "genres": ["Fantasy"],
        }),
        SimpleNamespace(title="Three", author="Author", metadata={
            "categories": ["History"], "genres": [],
        }),
    ]
    monkeypatch.setattr(eg, "_opds_fetch", root)
    monkeypatch.setattr(eg, "_opds_search_url", search)
    monkeypatch.setattr(eg, "_opds_load_acquisition", page)
    monkeypatch.setattr(eg, "_opds_items", lambda *_a: rows)
    session = SimpleNamespace(
        opds_feed_url=url, connected=True, page_size=50,
        last_search_warnings=[],
    )
    return session, calls


def test_genre_only_uses_explicit_genres(offline):
    session, calls = offline
    result = eg._opds_search(session, "Fantasy", field="genre")
    assert [row.title for row in result] == ["Two"]
    assert calls == ["root", "search", "page"]
    assert "partial_results" in session.last_search_warnings[0]


def test_category_only_uses_explicit_categories(offline):
    session, _ = offline
    assert [x.title for x in eg._opds_search(
        session, "Fantasy", field="category"
    )] == ["One"]


def test_subject_is_an_explicit_union_but_still_page_local(offline):
    session, _ = offline
    result = eg._opds_search(session, "Fantasy", field="subject")
    assert [row.title for row in result] == ["One", "Two"]
    assert len(session.last_search_warnings) == 1
    assert "page OPDS" in session.last_search_warnings[0]
