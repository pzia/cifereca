"""Baen Free Library current canonical product links, with no network I/O.

Source evidence: public Free Library category and the linked /1632.html product
page. These fixtures do not qualify server-side q/page/pageSize behavior.
"""
import httpx
import pytest

from cifereca_baen import BaenSession, UNVERIFIED_SEARCH_WARNING


def _search(body):
    calls = []
    session = BaenSession(
        credential_id="public", label="Baen",
        credentials={}, options={}, config={}, diagnostics=None,
        transport=None,
    )
    session.client.close()
    session.client = httpx.Client(transport=httpx.MockTransport(
        lambda request: (calls.append(request) or httpx.Response(200, text=body))
    ))
    session.connect()
    try:
        rows = session.search("Eric Flint")
        return rows, session.last_search_warnings[:], calls
    finally:
        session.close()


FREE_PAGE = '<h1>Free Library</h1><div>Showing 1-20 of 82</div>'


def test_current_free_library_links_are_not_silently_lost():
    rows, warnings, calls = _search(
        FREE_PAGE
        + '<a class="product-item-link" href="/1632.html">1632</a>'
        + '<a href="https://www.baen.com/a-desert-called-peace.html" '
          'class="product-item-link"><span>A Desert Called Peace</span></a>'
        + '<a class="product-item-link" href="/1632.html">1632</a>'
    )
    assert len(calls) == 1 and calls[0].method == "GET"
    assert [row.title for row in rows] == ["1632", "A Desert Called Peace"]
    assert [row.friendly_url for row in rows] == [
        "https://www.baen.com/1632.html",
        "https://www.baen.com/a-desert-called-peace.html",
    ]
    assert all(row.format is None for row in rows)  # detail alone may attest EPUB\n    assert all(row.source_collection == "public" and not row.owned
               and not row.downloadable and row.download_url is None
               for row in rows)
    assert warnings == [UNVERIFIED_SEARCH_WARNING]


@pytest.mark.parametrize("anchor", [
    '<a href="/buy.html">Buy</a>',
    '<a href="/1632.html">1632</a>',  # no verified product-title class
    '<a class="product-item-link" href="https://outside.test/1632.html">1632</a>',
    '<a class="product-item-link" href="/1632.html?token=opaque">1632</a>',
    '<a class="product-item-link" href="/customer/account.html">Account</a>',
    '<a class="product-item-link" href="/products/1632.html">1632</a>',
    '<a class="product-item-link" href="javascript:alert(1)">Click</a>',
])
def test_navigation_foreign_private_and_unmarked_links_not_inferred(anchor):
    rows, warnings, calls = _search(FREE_PAGE + anchor)
    assert rows == []
    assert len(calls) == 1
    assert warnings == [
        UNVERIFIED_SEARCH_WARNING,
        "partial_results : Baen : absence de résultats non attestée",
    ]


@pytest.mark.parametrize("page", [
    '<html><h1>News</h1>Showing 1-20 of 82</html>',
    '<html><h1>Free Library</h1>Other page without listing count</html>',
])
def test_product_link_on_other_pages_does_not_become_free_book(page):
    rows, warnings, calls = _search(
        page + '<a href="/1632.html" class="product-item-link">1632</a>'
    )
    assert rows == []
    assert len(calls) == 1
    assert len(warnings) == 2


def test_legacy_links_remain_readable_and_partial():
    rows, warnings, calls = _search(
        '<a href="/allbooks/example-book">Legacy attested fixture</a>'
    )
    assert len(rows) == 1
    assert rows[0].friendly_url == "https://www.baen.com/allbooks/example-book"
    assert warnings == [UNVERIFIED_SEARCH_WARNING]
    assert len(calls) == 1
