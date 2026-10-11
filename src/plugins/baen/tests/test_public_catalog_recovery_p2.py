"""P2 #764: Baen public catalogue migration, no fabricated real DOM fixtures.

Markup below is explicitly SYNTHETIC. Live public evidence: Baen currently
shows a populated catalog/category page, and top-level *.html product URLs.
Before claiming DOM compatibility, a sanitized *real* fixture is still needed.
"""
from types import SimpleNamespace
import pytest
from cifereca_baen_catalog import FREE_LIBRARY, catalog_cards, public_product_url
from cifereca_baen import BaenSession
from cifereca.plugin_api import ProviderError


CATALOG = """
<html><head><title>Free Library - Categories</title></head><body>
<nav><a href="/allbooks/category/index/id/2012">All Books</a>
<a href="/faq.html">FAQ</a><a href="/account/login">Login</a></nav>
<div class="products wrapper grid products-grid">
  <ol class="products list items product-items">
    <li class="item product product-item">
      <div class="product-item-info"><img src="/media/1632.jpg" alt="" /><strong class="product name product-item-name">
        <a class="product-item-link" href="https://www.baen.com/1632.html">1632</a>
      </strong><a class="action tocart" href="/checkout/cart/add">Add to cart</a></div>
    </li>
    <li class="item product product-item"><strong class="product name product-item-name">
      <a class="product-item-link" href="/a-desert-called-peace.html"><span>A Desert</span>
      Called Peace</a>
    </strong><a href="https://evil.example/other.html">Invalid</a></li>
    <li class="product-item"><strong class="product-item-name">
      <a class="product-item-link" href="/1632.html">1632 duplicate</a></strong></li>
  </ol>
</div>
<footer><a class="product-item-link" href="/outside-the-product-card.html">Navigation</a>
<a class="product-item-link" href="javascript:alert(1)">Attack</a></footer>
</body></html>
"""


class FakeResponse:
    def __init__(self, text=CATALOG, url=FREE_LIBRARY):
        self.text, self.url = text, url
    def raise_for_status(self):
        return None


class Client:
    def __init__(self, text=CATALOG, url=FREE_LIBRARY):
        self.text, self.url, self.calls = text, url, []
    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return FakeResponse(self.text, self.url)
    def close(self):
        pass


def session(client):
    obj = BaenSession(
        credential_id="public", label="", credentials={}, options={},
        config={}, diagnostics=None, transport=None,
    )
    obj.client.close()
    obj.client = client
    obj.connect()
    return obj


def test_current_category_endpoint_and_real_product_path_contract():
    assert FREE_LIBRARY == "https://www.baen.com/catalog/category/view/s/free-library/id/2012"
    assert BaenSession._search_params("a desert", 1, 50) == {
        "p": 2, "product_list_limit": 36,
    }
    assert public_product_url("/1632.html") == "https://www.baen.com/1632.html"
    assert public_product_url("/a-desert-called-peace.html") == (
        "https://www.baen.com/a-desert-called-peace.html"
    )


def test_synthetic_product_cards_omit_nav_cart_duplicates_and_external():
    assert catalog_cards(CATALOG) == (
        ("https://www.baen.com/1632.html", "1632"),
        ("https://www.baen.com/a-desert-called-peace.html", "A Desert Called Peace"),
    )


@pytest.mark.parametrize("href", [
    "javascript:alert(1)", "http://www.baen.com/1632.html",
    "https://evil.example/1632.html", "//evil.example/1632.html",
    "https://evil.example@www.baen.com/1632.html",
    "https://www.baen.com:444/1632.html",
    "https://www.baen.com/1632.html?token=private",
    "https://www.baen.com/1632.html#customer", "/allbooks/category/index/id/2012",
    "/downloads/ebook.epub", "/account/login", "/free/../1632.html",
    "/faq.html", "",
])
def test_product_url_fails_closed(href):
    assert public_product_url(href) == ""


def test_search_returns_only_attested_free_category_books_on_current_page():
    client = Client()
    provider = session(client)
    try:
        titles = provider.search("", page=0)
        assert [b.title for b in titles] == ["1632", "A Desert Called Peace"]
        assert [b.friendly_url for b in titles] == [
            "https://www.baen.com/1632.html",
            "https://www.baen.com/a-desert-called-peace.html",
        ]
        assert all(b.downloadable is False and b.owned is False for b in titles)
        assert all(b.metadata["catalog_scope"] == "public_page_only" for b in titles)
        assert client.calls[0][0] == FREE_LIBRARY
        assert client.calls[0][1]["params"] == {"p": 1, "product_list_limit": 36}
        # This is only a *page-local* filter, never a claim that Baen's
        # entire catalog was searched server-side.
        assert [b.title for b in provider.search("desert")] == ["A Desert Called Peace"]
        assert provider.search("unknown title") == []
    finally:
        provider.close()


@pytest.mark.parametrize("document", [
    "<html><body><a href='/allbooks/category/index/id/2012'>Free Library</a></body></html>",
    "<html>Login Required</html>",
    "<html>Captcha challenge</html>",
    "<html><a href='https://evil.example/1632.html' class='product-item-link'>Hijack</a></html>",
])
def test_unexpected_layout_or_login_never_becomes_false_empty_catalogue(document):
    provider = session(Client(document))
    try:
        with pytest.raises(ProviderError, match="structure"):
            provider.search("")
    finally:
        provider.close()


def test_explicit_empty_page_allowed_and_redirect_to_account_rejected():
    provider = session(Client("<div>No results</div>"))
    try:
        assert provider.search("", page=9) == []
    finally:
        provider.close()
    redirected = session(Client(CATALOG, "https://www.baen.com/customer/account/login/"))
    try:
        with pytest.raises(ProviderError, match="déplacé"):
            redirected.search("")
    finally:
        redirected.close()


def test_bounded_html_page_and_no_extra_http_or_personal_account_access():
    provider = session(Client("<li class='product-item'>" + "x" * 1_500_001 + "</li>"))
    try:
        with pytest.raises(ProviderError):
            provider.search("")
        assert len(provider.client.calls) == 1
        assert "/catalog/category/" in provider.client.calls[0][0]
    finally:
        provider.close()
