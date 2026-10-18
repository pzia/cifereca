"""#1886: faceted navigation is based on observed public source links only.

All fixtures are offline; no test claims that live robots, CGU or the
federated Core UI has been qualified for Numilog public catalogue browsing.
"""
from dataclasses import replace
from pathlib import Path
import json

import pytest

from cifereca.plugin_api import ProviderCapabilityError, ProviderError
import cifereca_numilog_public_provider as provider
from cifereca_numilog_public import GRID_ID, parse_public_search

FIXTURES = Path(__file__).parent / "fixtures"
HTML = (FIXTURES / "public_search_jefferson_20261016.html").read_text()
EXPECTED = json.loads((FIXTURES / "public_search_jefferson_20261016.expected.json").read_text())
SEARCH_URL = EXPECTED["source_url"]
FIRST = HTML[
    HTML.index('<tr class="LivreDuThemeDetail">'):
    HTML.index('<tr class="LivreDuThemeDetail">',
               HTML.index('<tr class="LivreDuThemeDetail">') + 1)
]


def session():
    s = provider.PLUGIN.create_session(
        credential_id="__public__", label="Catalogue",
        credentials={}, options={}, config={}, storage=None, diagnostics=None)
    s.connect()
    return s


def fake_search(monkeypatch):
    calls = []
    def load(query, *, page=0, field="all"):
        calls.append((query, page, field))
        assert query == "jefferson" and page == 0 and field == "all"
        return parse_public_search(HTML, SEARCH_URL)
    monkeypatch.setattr(provider, "fetch_public_search", load)
    return calls


def one_card_html(total=1):
    return (f'<span class="ResultatDetail">{total}</span>'
            f'<table id="{GRID_ID}"><tbody>{FIRST}</tbody></table>')


def test_browse_keeps_page_count_facets_next_link_and_commercial_only_results(monkeypatch):
    calls = fake_search(monkeypatch)
    s = session()
    try:
        base = s.browse_public_search("jefferson")
        assert calls == [("jefferson", 0, "all")]
        assert base.total_results == 242 and base.page_index == 0
        assert base.next_url == EXPECTED["pagination_links"][0]["url"]
        assert list(base.facets) == EXPECTED["facets"]
        assert list(base.sort_options) == EXPECTED["sort_options"]
        assert len(base.items) == 20
        assert all(row.source_provider == "numilog-catalog"
                   and row.source_credential == "__public__"
                   and row.source_collection == "public_catalog"
                   and row.owned is False and row.downloadable is False
                   and row.download_url is None for row in base.items)
        # Generic Core search reuses exact same cache entry, not another HTTP.
        assert s.search("jefferson")[0].record_id == base.items[0].record_id
        assert calls == [("jefferson", 0, "all")]
        assert s.search_capabilities.fields == ("all",)  # No invented ISBN/author mode.
    finally:
        s.close()


def test_follow_observed_facet_then_page_with_public_evidence_and_no_private_http(monkeypatch):
    fake_search(monkeypatch)
    loaded = []
    def load(url):
        loaded.append(url)
        # No faked fulfillment URLs; the fixture card remains non-owned.
        total = 2 if "GRID_PAGE=1" in url else 1
        return parse_public_search(one_card_html(total), url)
    monkeypatch.setattr(provider, "fetch_public_search_url", load)
    s = session()
    try:
        base = s.browse_public_search("jefferson")
        facet = next(f for f in base.facets if f["parameter"] == "fltrLang")
        filtered = s.follow_public_link(facet["url"])
        assert loaded == [facet["url"]]
        assert filtered.total_results == 1 and len(filtered.items) == 1
        assert filtered.items[0].metadata["source_url"] == facet["url"]
        assert filtered.items[0].owned is False and filtered.items[0].download_url is None
        # Simulate >1 s elapsed: public budget allows only a 2-request burst.
        with s._public_cache._lock:
            s._public_cache._updated -= 1.1
        # Original page's observed GET GRID_PAGE=1 is also a permitted link.
        next_page = s.follow_public_link(base.next_url)
        assert next_page.page_index == 1 and next_page.total_results == 2
        assert loaded == [facet["url"], base.next_url]
        # Cache prevents repeat HTTP for explicit navigation.
        assert s.follow_public_link(facet["url"]).items == filtered.items
        assert loaded == [facet["url"], base.next_url]
    finally:
        s.close()


@pytest.mark.parametrize("unproven", [
    "https://www.numilog.com/Pages/Recherche/ResultatRecherche.aspx?mode=brSimple&query=jefferson&fltrLang=UNOBSERVED",
    "https://www.numilog.com/Pages/Recherche/ResultatRecherche.aspx?mode=brSimple&query=jefferson&GRID_PAGE=99",
    "https://www.numilog.com/Pages/Recherche/ResultatRecherche.aspx?mode=brSimple&query=jefferson&auth=PRIVATE_MARKER",
    "https://evil.invalid/Pages/Recherche/ResultatRecherche.aspx?mode=brSimple&query=jefferson",
    "https://www.numilog.com/1958476/jefferson-prend-des-risques.ebook",
    None, 17, False,
])
def test_no_arbitrary_field_signed_url_or_unobserved_page_is_sent(monkeypatch, unproven):
    fake_search(monkeypatch)
    network = []
    monkeypatch.setattr(provider, "fetch_public_search_url",
                        lambda url: network.append(url))
    s = session()
    try:
        s.browse_public_search("jefferson")
        with pytest.raises(ProviderCapabilityError, match="unverified_navigation") as exc:
            s.follow_public_link(unproven)
        assert "PRIVATE_MARKER" not in str(exc.value)
        assert network == []
    finally:
        s.close()


def test_navigation_expires_and_close_reconnect_invalidates_without_extra_get(monkeypatch):
    fake_search(monkeypatch)
    get_calls = []
    monkeypatch.setattr(provider, "fetch_public_search_url",
                        lambda url: get_calls.append(url))
    s = session()
    try:
        url = s.browse_public_search("jefferson").next_url
        with s._navigation_lock:
            s._navigation_urls[url] = 0.0
        with pytest.raises(ProviderCapabilityError, match="unverified_navigation"):
            s.follow_public_link(url)
        assert not get_calls
        # Even if an old page is retained by a caller, reopening authorizes
        # nothing until a new browse explicitly returns fresh attested links.
        s.close()
        s.connect()
        with pytest.raises(ProviderCapabilityError, match="unverified_navigation"):
            s.follow_public_link(url)
        assert not get_calls
    finally:
        s.close()


def test_bad_upstream_navigation_proof_fails_closed_without_leaking_url(monkeypatch):
    page = parse_public_search(HTML, SEARCH_URL)
    secret = SEARCH_URL + "&auth=PRIVATE_MARKER"
    fake_search(monkeypatch)
    monkeypatch.setattr(provider, "fetch_public_search",
                        lambda *a, **kw: replace(page, facets=({"url": secret},)))
    s = session()
    try:
        with pytest.raises(ProviderError, match="unverified_navigation") as exc:
            s.browse_public_search("jefferson")
        assert "PRIVATE_MARKER" not in str(exc.value)
        with pytest.raises(ProviderCapabilityError, match="unverified_navigation"):
            s.follow_public_link(secret)
    finally:
        s.close()


def test_late_inflight_navigation_result_cannot_survive_close(monkeypatch):
    fake_search(monkeypatch)
    s = session()
    try:
        source = s.browse_public_search("jefferson").next_url
        def close_during_load(url):
            s.close()
            return parse_public_search(one_card_html(2), url)
        monkeypatch.setattr(provider, "fetch_public_search_url", close_during_load)
        with pytest.raises(ProviderError, match="invalidated"):
            s.follow_public_link(source)
        s.connect()
        with pytest.raises(ProviderCapabilityError, match="unverified_navigation"):
            s.follow_public_link(source)
    finally:
        s.close()


def test_public_cache_budget_still_limits_facet_following(monkeypatch):
    fake_search(monkeypatch)
    requests = []
    monkeypatch.setattr(provider, "fetch_public_search_url",
                        lambda url: requests.append(url))
    s = session()
    try:
        source = s.browse_public_search("jefferson").next_url
        # One legal browse does not authorize unbounded page scanning.
        with s._public_cache._lock:
            s._public_cache._tokens = 0.0
            s._public_cache._updated = s._public_cache._clock()
        with pytest.raises(ProviderError, match="budget_exhausted"):
            s.follow_public_link(source)
        assert requests == []
    finally:
        s.close()
