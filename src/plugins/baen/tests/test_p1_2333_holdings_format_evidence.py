"""#2333: a Baen product page is not evidence of a downloadable EPUB.

Synthetic public HTML only. No Baen HTTP, purchasing, login or file writes.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

import httpx
import pytest

from cifereca.plugin_api import ProviderError
from cifereca_baen import BaenSession


DETAIL = "https://www.baen.com/allbooks/synthetic-book"


class FakeResponse:
    status_code = 200

    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        pass


class FakeClient:
    def __init__(self, text=None, error=None):
        self.text = text
        self.error = error
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        if self.error is not None:
            raise self.error
        return FakeResponse(self.text)

    def close(self):
        pass


class NoFetch:
    def __init__(self):
        self.calls = []

    def fetch(self, source, destination):
        self.calls.append(source)
        raise AssertionError("The source has no attested EPUB")


def session(page, *, error=None):
    transport = NoFetch()
    obj = BaenSession(
        credential_id="public", label="", credentials={}, options={},
        config={}, diagnostics=None, transport=transport,
    )
    obj.client.close()
    obj.client = FakeClient(page, error)
    obj.connect()
    return obj, transport


def book():
    return NS(
        source_provider="baen", friendly_url=DETAIL,
        title="Synthetic book", record_id="synthetic-book",
        author=None, published_at=None, thumbnail_url=None,
        type="Livre numérique", metadata={},
    )


@pytest.mark.parametrize("page,expected_status,label", [
    ('<a href="/downloads/book.epub">Download EPUB</a>',
     "available", "Lien EPUB public affiché"),
    ('<p>Only PDF available</p>',
     "unknown", "Aucun lien EPUB"),
    ('<a href="/downloads/book.pdf">Download EPUB</a>',
     "unknown", "Aucun lien EPUB"),
    ('<a href="/downloads/book">Download</a>',
     "unknown", "format EPUB inconnu"),
])
def test_holdings_reflect_only_attested_public_epub_links(page, expected_status, label):
    obj, transport = session(page)
    try:
        detail = obj.holdings(book())
        assert detail.availability_status == expected_status
        assert label in detail.availability_label
        assert obj.client.calls == [DETAIL]
        assert transport.calls == []
    finally:
        obj.close()


@pytest.mark.parametrize("page", [
    "<html><p>No public download offered</p></html>",
    '<a href="/downloads/book.pdf">Download EPUB</a>',
])
def test_download_without_epub_offer_stops_after_one_detail_get(tmp_path, page):
    obj, transport = session(page)
    try:
        with pytest.raises(ProviderError, match="aucun lien EPUB public explicite"):
            obj.download_direct(book(), tmp_path)
        assert obj.client.calls == [DETAIL]
        assert transport.calls == []
        assert list(tmp_path.iterdir()) == []
    finally:
        obj.close()


def test_explicit_epub_offer_wins_over_earlier_untyped_download():
    page = (
        '<a href="/downloads/ambiguous">Download</a>'
        '<a href="/downloads/book.epub">Download EPUB</a>'
    )
    obj, transport = session(page)
    try:
        detail = obj.holdings(book())
        assert detail.availability_status == "available"
        assert obj.client.calls == [DETAIL]
        assert transport.calls == []
    finally:
        obj.close()


def test_unavailable_detail_does_not_claim_available():
    request = httpx.Request("GET", DETAIL)
    obj, transport = session(None, error=httpx.ReadTimeout("timeout", request=request))
    try:
        detail = obj.holdings(book())
        assert detail.availability_status == "unknown"
        assert "inaccessible" in detail.availability_label
        assert obj.client.calls == [DETAIL]
        assert transport.calls == []
    finally:
        obj.close()


@pytest.mark.parametrize("href", [
    "/customer/account/login",
    "/customer/account/login.epub",
    "/account/profile.epub",
    "/checkout/cart/add",
    "/cart/download.epub",
    "/sso/private.epub",
    "/admin/content.epub",
    "/oauth/authorize.epub",
    "/download/index/owned/id/123",
    "/download/index/unverified/id/123",
    "/downloads/book.epub?session=DO_NOT_SEND",
    "/downloads/book.epub#DO_NOT_SEND",
    "/downloads/%2e%2e/customer/account/login.epub",
    "/downloads/../customer/account/login.epub",
    "http://[bad-ipv6",
    "https://elsewhere.example/book.epub",
])
def test_login_signed_and_malformed_html_offers_are_not_public(href, tmp_path):
    # One HTML label cannot authorize a different/account-bound HTTP route.
    obj, transport = session(f'<a href="{href}">Download EPUB</a>')
    try:
        observed = obj.holdings(book())
        assert observed.availability_status == "unknown"
        assert "Aucun lien EPUB public" in observed.availability_label
        with pytest.raises(ProviderError, match="aucun lien EPUB public"):
            obj.download_direct(book(), tmp_path)
        assert obj.client.calls == [DETAIL, DETAIL]
        assert transport.calls == []
        assert list(tmp_path.iterdir()) == []
    finally:
        obj.close()


def test_explicit_unsigned_public_epub_wins_over_private_links():
    page = (
        '<a href="/customer/account/login.epub">Download EPUB</a>'
        '<a href="/downloads/book.epub?token=PRIVATE">Download EPUB</a>'
        '<a href="/downloads/public-book.epub">Download EPUB</a>'
    )
    obj, transport = session(page)
    try:
        result = obj.holdings(book())
        assert result.availability_status == "available"
        assert "Lien EPUB public affiché" in result.availability_label
        assert obj.client.calls == [DETAIL]
        assert transport.calls == []
    finally:
        obj.close()


def test_unsigned_public_generic_download_stays_format_unknown():
    obj, transport = session('<a href="/downloads/book">Download</a>')
    try:
        result = obj.holdings(book())
        assert result.availability_status == "unknown"
        assert "format EPUB inconnu" in result.availability_label
        assert obj.client.calls == [DETAIL]
        assert transport.calls == []
    finally:
        obj.close()
