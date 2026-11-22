"""#6397/#7163: Numilog's observed public result HTML is not a book-specific URL.

Pure URL normalization and link projection, no supplier transport/session.
"""
import pytest

from cifereca.catalogue_link import catalogue_url, catalogue_notice_url, safe_document_link_url


@pytest.mark.parametrize("route", [
    "/Pages/Recherche/ResultatRecherche.aspx?mode=brSimple&query=jefferson",
    "/Pages/Recherche/ResultatRecherche.aspx?mode=Ra&RAAuthor=bor",
    "/Pages/Recherche/ResultatRecherche.aspx/",
    "/Pages/Recherche/%52esultatRecherche.aspx?query=jefferson",
])
def test_numilog_result_route_cannot_be_qualified_as_public_book_sheet(route):
    url = "https://www.numilog.com" + route
    assert catalogue_url({"friendly_url": url}) == url
    assert catalogue_notice_url({"friendly_url": url}) is None
    assert safe_document_link_url(url, relation="notice") is None
    assert safe_document_link_url(url, relation="catalogue") == url


def test_bona_fide_book_record_beats_an_unqualified_search_route():
    result = "https://www.numilog.com/Pages/Recherche/ResultatRecherche.aspx?query=roman"
    book = "https://www.numilog.com/123456/notice.ebook"
    row = {"friendly_url": result, "metadata": {"product_url": book}}
    assert catalogue_notice_url(row) == book
    assert safe_document_link_url(book, relation="notice") == book
