"""#6397: a generic Search route is never an exact public book notice.

No provider HTTP or user session. Public URL validation and presentation
boundaries must agree, including already-cached URLs and relative bases.
"""
from __future__ import annotations

import pytest

from cifereca.catalogue_link import (
    catalogue_notice_url, catalogue_url, safe_document_link_url,
)
from cifereca.plugin_api import document_links


@pytest.mark.parametrize("path", (
    "/Search.aspx?SC=EBOOKS&QUERY=SeriesTitle_idx%3ASaga",
    "/Search.aspx?field=SeriesTitle_idx",
    "/Search.svc/Search",
    "/index.php/search?q=roman",
    "/index.php/search.aspx?SC=CATALOGUE",
    "/%53earch.aspx?query=saga",
    "/search%2easpx",
    "/Search.aspx/",
    "/index.php/%73earch?q=book",
))
def test_search_endpoint_not_disguised_as_a_notice(path):
    url = "https://bibliotheques.paris.fr" + path
    row = {"provider_id": "paris", "friendly_url": url}
    assert catalogue_url(row) == url  # generic navigation remains possible
    assert catalogue_notice_url(row, provider="paris") is None
    assert safe_document_link_url(url, relation="notice") is None
    assert safe_document_link_url(url, relation="catalogue") == url
    assert document_links(row) == ()


@pytest.mark.parametrize("relative", (
    "/Search.aspx?SC=EBOOKS",
    "Search.aspx?SC=EBOOKS",
    "index.php/search?q=book",
))
def test_relative_search_route_cannot_be_relabelled_as_exact_notice(relative):
    assert catalogue_notice_url(
        {"friendly_url": relative}, base="https://bibliotheques.paris.fr/",
        provider="paris",
    ) is None


def test_generic_search_candidate_does_not_hide_exact_independent_html_sheet():
    url = "https://bibliotheques.paris.fr/notices/book-42"
    row = {
        "provider_id": "paris",
        "friendly_url": "https://bibliotheques.paris.fr/Search.aspx?query=book",
        "metadata": {"product_url": url},
    }
    assert catalogue_notice_url(row, provider="paris") == url
    assert [link.url for link in document_links(row)] == [url]
    assert safe_document_link_url(url, relation="notice") == url
