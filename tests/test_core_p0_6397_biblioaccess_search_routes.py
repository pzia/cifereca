"""#6397: BiblioAccess tenant search pages must not become book notices.

Only safe URL projection; no provider request, session or browser launch.
"""
import pytest

from cifereca.catalogue_link import (
    catalogue_url, catalogue_notice_url, safe_document_link_url,
)


@pytest.mark.parametrize("route", [
    "/1409/Catalog/Search?query=roman",
    "/1409/Catalog/SearchResultSummary?query=roman",
    "/1409/Catalog/%53earch?query=roman",
    "/1409/Catalog/Search/",
    "/Catalog/Search?query=roman",
])
def test_search_or_summary_route_is_navigation_not_book_sheet(route):
    url = "https://www.biblioaccess.com" + route
    assert catalogue_url({"friendly_url": url}) == url
    assert catalogue_notice_url({"friendly_url": url}) is None
    assert safe_document_link_url(url, relation="notice") is None
    assert safe_document_link_url(url, relation="catalogue") == url


def test_search_link_never_hides_independently_qualified_book_sheet():
    detail = "https://www.biblioaccess.com/1409/Catalog/Book/4242/example"
    row = {
        "friendly_url": "https://www.biblioaccess.com/1409/Catalog/Search?query=roman",
        "metadata": {"product_url": detail},
    }
    assert catalogue_notice_url(row) == detail
    assert safe_document_link_url(detail, relation="notice") == detail
