"""#1917: untrusted COPS Atom navigation cannot GET same-origin account actions.

Offline MockTransport only. Explicitly observed Calibre feed/OpenSearch routes
remain available; no real network or acquisition is performed.
"""
import httpx
import pytest

from cifereca.plugin_api import ProviderError
from cifereca_ebooksgratuits import EbooksSession, _safe_opds_link

HOST = "https://opds2.ebooksgratuits.org"
FEED = HOST + "/index.php/feed"
ATOM = "http://www.w3.org/2005/Atom"


@pytest.mark.parametrize("href", (
    "/index.php/account/logout",
    "/index.php/profile/delete.xml",
    "/index.php/download/settings",
    "/index.php/feed/%2e%2e/account/logout",
    "/index.php/feed/%2Faccount/logout",
    "/index.php/feed/%255caccount/logout",
    "/index.php/feed/%5caccount/logout",
    "/index.php/admin/feed",
    "/admin/feed.php",
    "/admin/thing.xml",
))
def test_action_shaped_feed_links_rejected_before_request(href):
    assert _safe_opds_link(FEED, href, "feed") is None


@pytest.mark.parametrize("href", (
    "/index.php/feed",
    "/index.php/feed/all",
    "/index.php/feed/search?q=novel",
    "/index.php/feed/search?q=jean%20valjean",
    "/index.php/feed?p=2",
    "/index.php/opensearch.xml",
    "/feed",
    "/feed.php",
    "/index.php/feed.php",
))
def test_only_supported_opds_navigation_routes_remain_allowed(href):
    assert _safe_opds_link(FEED, href, "feed") == HOST + href


def test_malicious_next_link_is_rejected_without_fetching_account_page():
    calls = []
    root = f'''<feed xmlns="{ATOM}">
    <link rel="search" type="application/atom+xml"
    href="/index.php/feed/search?q={{searchTerms}}"/></feed>'''
    search = f'''<feed xmlns="{ATOM}">
    <entry><id>urn:one</id><title>Book one</title>
    <link rel="http://opds-spec.org/acquisition/open-access"
    type="application/epub+zip" href="/index.php/fetch/1.epub"/></entry>
    <link rel="next" href="/index.php/account/logout"/></feed>'''

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/index.php/feed":
            return httpx.Response(200, text=root)
        if request.url.path == "/index.php/feed/search":
            return httpx.Response(200, text=search)
        pytest.fail("OPDS route crossed into private action: " + request.url.path)

    obj = EbooksSession(
        credential_id="public", label="", credentials={}, options={},
        config={}, diagnostics=None, transport=None,
    )
    obj.client.close()
    obj.client = httpx.Client(transport=httpx.MockTransport(handler), trust_env=False)
    obj.connect()
    try:
        with pytest.raises(ProviderError, match="invalid_opds_pagination"):
            obj.search("Book", page=1)
        assert calls == ["/index.php/feed", "/index.php/feed/search"]
    finally:
        obj.close()
