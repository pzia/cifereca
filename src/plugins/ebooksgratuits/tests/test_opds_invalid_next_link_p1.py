"""#1917: a malformed declared COPS/OPDS next page is not an empty page."""
from types import SimpleNamespace
import xml.etree.ElementTree as ET

import pytest

import cifereca_ebooksgratuits as eg
from cifereca.plugin_api import ProviderError

FEED = "https://opds2.ebooksgratuits.org/index.php/feed"
ATOM = "http://www.w3.org/2005/Atom"


def xml(*links):
    return ET.fromstring(
        '<feed xmlns="' + ATOM + '">' + ''.join(links) + '</feed>'
    )


@pytest.mark.parametrize("href", [
    "https://evil.test/private",
    "http://opds2.ebooksgratuits.org/index.php/feed?page=2",
    "https://opds2.ebooksgratuits.org:444/index.php/feed?page=2",
    "https://opds2.ebooksgratuits.org/index.php/feed?x=private\\foo",
    "javascript:private",
    "https://opds2.ebooksgratuits.org/Pages/Compte/MesLivres.aspx",
])
def test_declared_but_invalid_next_does_not_mean_no_results(href):
    import html
    page = xml('<link rel="next" href="' + html.escape(href, quote=True) + '"/>')
    with pytest.raises(ProviderError, match="invalid_opds_pagination") as caught:
        eg._opds_find_link(page, FEED, "next")
    assert "private" not in str(caught.value).casefold()


def test_conflicting_next_links_are_ambiguous_not_first_wins():
    page = xml(
        '<link rel="next" href="/index.php/feed?page=2"/>',
        '<link rel="next" href="/index.php/feed?page=3"/>',
    )
    with pytest.raises(ProviderError, match="invalid_opds_pagination"):
        eg._opds_find_link(page, FEED, "next")


def test_one_valid_next_link_and_real_end_keep_contract():
    no_next = xml('<link rel="self" href="/index.php/feed"/>')
    assert eg._opds_find_link(no_next, FEED, "next") == (None, None, None)
    one = xml('<link rel="next" href="/index.php/feed?page=2"/>')
    result, *_ = eg._opds_find_link(one, FEED, "next")
    assert result == "https://opds2.ebooksgratuits.org/index.php/feed?page=2"


def test_page_one_request_refuses_broken_link_without_network(monkeypatch):
    calls = []
    root = xml('<link rel="search" href="/index.php/feed/search?q={searchTerms}"/>')
    broken = xml('<link rel="next" href="https://evil.test/private"/>')
    monkeypatch.setattr(eg, "_opds_fetch",
                        lambda _s, _u: (calls.append("root") or root, FEED))
    monkeypatch.setattr(eg, "_opds_search_url",
                        lambda *_: FEED + "/search?q=livre")
    monkeypatch.setattr(eg, "_opds_load_acquisition",
                        lambda *_: (calls.append("page0") or broken, FEED))
    session = SimpleNamespace(connected=True,opds_feed_url=FEED,page_size=50,
                              last_search_warnings=[])
    with pytest.raises(ProviderError, match="invalid_opds_pagination"):
        eg._opds_search(session, "livre", page=1)
    assert calls == ["root", "page0"]
    assert session.last_search_warnings == []
