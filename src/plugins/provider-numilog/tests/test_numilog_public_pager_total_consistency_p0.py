"""#1886: public Numilog count and pager must agree before proving exhaustion.

All cases originate from a captured/sanitized public search card; no network.
"""
from pathlib import Path

import pytest

from cifereca_numilog_public import (
    GRID_ID, PublicSearchParseError, parse_public_search,
)

URL = (
    "https://www.numilog.com/Pages/Recherche/ResultatRecherche.aspx"
    "?mode=brSimple&query=jefferson"
)
HTML = (
    Path(__file__).with_name("fixtures")
    / "public_search_jefferson_20261016.html"
).read_text()
START = HTML.index('<tr class="LivreDuThemeDetail">')
NEXT = HTML.index('<tr class="LivreDuThemeDetail">', START + 1)
CARD = HTML[START:NEXT]


def sample(*, pager):
    return (
        '<span class="ResultatDetail">1</span>'
        f'<table id="{GRID_ID}"><tbody>{CARD}</tbody></table>'
        + '<table><tbody><tr class="pagination"><td>'
        + '<span class="PagerCurrentPage">1</span>'
        + pager + '</td></tr></tbody></table>'
    )


@pytest.mark.parametrize("page_target", [1, 2, 99])
def test_advertised_future_page_contradicts_complete_one_book_total(page_target):
    suffix = f"&amp;GRID_PAGE={page_target}"
    pager = (
        '<a href="/Pages/Recherche/ResultatRecherche.aspx'
        '?mode=brSimple&amp;query=jefferson'
        + suffix + '">Autre page</a>'
    )
    with pytest.raises(PublicSearchParseError, match="inconsistent_pagination"):
        parse_public_search(sample(pager=pager), URL)


def test_self_page_link_is_not_future_pagination():
    pager = (
        '<a href="/Pages/Recherche/ResultatRecherche.aspx'
        '?mode=brSimple&amp;query=jefferson">1</a>'
    )
    p = parse_public_search(sample(pager=pager), URL)
    assert p.total_results == len(p.items) == 1
    assert p.complete is True
    assert p.next_url is None


def test_normal_captured_multi_page_remains_explicitly_partial():
    p = parse_public_search(HTML, URL)
    assert len(p.items) == 20 and p.total_results == 242
    assert p.complete is False
    assert p.next_url is not None
