"""P2 #764: Numilog public catalogue URLs never disclose account links.

The public /1171334/blackbone--...ebook URL format is documented from
Numilog's public website. The HTML used below is synthetic, not a claim of
captured authenticated account HTML.
"""
from __future__ import annotations

import pytest

from cifereca_numilog import NumilogSession
from cifereca_numilog_html import LIBRARY, parse_library_page, public_book_url


CANONICAL = "https://www.numilog.com/1171334/blackbone--coltan-song-tome-1--des-15-ans.ebook"
OTHER = "https://www.numilog.com/1956345/le-piege-de-sainte-soline--megabassines--la-lutte-pour-l-eau.ebook"


def card(book_link, lid="12"):
    return (
        '<div><a href="' + book_link + '"><span class="pTitle">Blackbone</span></a>'
        '<a class="LnkDownloadBook" href="/Pages/Compte/readBook.ashx?lid=' + lid +
        '&amp;m=download">Télécharger</a></div>'
    )


def table(*cards):
    return '<html><body><table id="myTable">' + "".join(cards) + '</table></body></html>'


@pytest.mark.parametrize("value,expected", [
    (CANONICAL, CANONICAL),
    ("/1171334/blackbone--coltan-song-tome-1--des-15-ans.ebook", CANONICAL),
    (OTHER, OTHER),
    ("/livres/notice-12", "https://www.numilog.com/livres/notice-12"),
    ("/ebooks/notice-34?ref=library",
     "https://www.numilog.com/ebooks/notice-34?ref=library"),
    ("/12345/book.EBOOK", "https://www.numilog.com/12345/book.EBOOK"),
])
def test_recognized_product_url_formats_preserved(value, expected):
    assert public_book_url(value) == expected


@pytest.mark.parametrize("value", [
    None, "", " ", "https://evil.example/123/novel.ebook",
    "http://www.numilog.com/1171334/blackbone.ebook",
    "https://www.numilog.com@evil.example/123/novel.ebook",
    "https://evil.example@www.numilog.com/123/novel.ebook",
    "https://www.numilog.com:444/123/novel.ebook",
    "//evil.example/123/novel.ebook", "javascript:alert(1)",
    "file:///123/novel.ebook", "https://www.numilog.com/123/novel.ebook#customer",
    "https://www.numilog.com/123/novel.ebook?access_token=private",
    "https://www.numilog.com/123/novel.ebook?ref=library",
    "https://www.numilog.com/ebooks/notice-34?ref=library&token=private",
    "/ebooks/notice-34?ref=library&ref=library",
    "/ebooks/notice-34?session=secret",
    "/Pages/Compte/MesLivres.aspx",
    "/Pages/Compte/readBook.ashx?lid=12",
    "/customer/account/login",
    "/checkout/cart", "/catalog/category",
    "/1234/../1234/book.ebook", "/1234/%2e%2e/book.ebook",
    " https://www.numilog.com/123/book.ebook",
    "https://www.numilog.com/123/book.ebook\r\nAuthorization: token",
    "/1234/book.epub", "/1234/book.pdf",
])
def test_public_url_rejects_account_links_and_secret_bearing_variants(value):
    assert public_book_url(value) is None


def test_library_parser_keeps_attested_book_link_and_drops_account_urls():
    html = table(
        card("/1171334/blackbone--coltan-song-tome-1--des-15-ans.ebook", "12"),
        card("/customer/account/login", "34"),
        card("/1234/a-book.ebook?token=secret", "56"),
        card("/ebooks/notice-34?ref=library", "78"),
    )
    rows, cursor = parse_library_page(html)
    assert cursor is None
    assert [row["lid"] for row in rows] == ["12", "34", "56", "78"]
    assert [row["public_url"] for row in rows] == [
        CANONICAL, None, None, "https://www.numilog.com/ebooks/notice-34?ref=library",
    ]
    # Download links are account-bound and are still retained for the existing
    # explicit acquisition contract, but never become friendly URLs.
    assert all("readBook.ashx" in row["href"] for row in rows)


def session(account):
    return NumilogSession(
        credential_id=account, label="Numilog", credentials={},
        options={}, config={}, diagnostics=None,
    )


def raw(public_url):
    return {
        "lid":"12", "variant":"()", "title":"Blackbone",
        "cover":None, "href":LIBRARY, "public_url":public_url,
    }


def test_projection_keeps_only_verified_links_with_attested_evidence():
    a = session("alice")
    b = session("bob")
    try:
        x = a._normalize(raw(CANONICAL))
        y = b._normalize(raw(CANONICAL))
        assert x.friendly_url == CANONICAL
        assert y.friendly_url == CANONICAL  # canonical public URL not a credential
        assert x.id != y.id  # account scope still separate
        assert x.metadata["public_url_evidence"] == "numilog.library_title_link"
        bad = a._normalize(raw(CANONICAL + "?session_token=abc"))
        assert bad.friendly_url is None
        assert "public_url_evidence" not in bad.metadata
        assert bad.owned is True and bad.downloadable is True
    finally:
        a.close()
        b.close()


def test_old_persisted_item_is_revalidated_on_holdings_without_network():
    provider = session("alice")
    try:
        item = provider._normalize(raw(CANONICAL))
        item.friendly_url = "https://www.numilog.com/123/book.ebook?token=secret"
        detail = provider.holdings(item)
        assert detail.availability_status == "owned"
        assert item.friendly_url is None
        # No account auth or HTTP is performed in this local detail operation.
        item.friendly_url = OTHER
        provider._canonical_item(item)
        assert item.friendly_url == OTHER
    finally:
        provider.close()
