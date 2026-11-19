"""Guarded direct product fetch, actual public parser and shared cache, fake HTTP."""
import pytest

import cifereca_numilog_public_client as client
from cifereca_numilog_public_cache import PublicCatalogueCache
from cifereca.plugin_api import ProviderCapabilityError, ProviderError
from tests.test_public_detail_transport_p0 import URL, AnonymousClient


def fetch(cache, guard, *, url=URL, title="Fixture"):
    return client.fetch_guarded_purchase_detail(cache, url, title, check_authority=guard)


def requests():
    return [item for item in AnonymousClient.requests if item[0] == "request"]


@pytest.fixture
def cache(monkeypatch):
    AnonymousClient.requests = []
    monkeypatch.setattr(client.HTTPX_TRANSPORT, "client", AnonymousClient)
    return PublicCatalogueCache(clock=lambda: 100)


def test_exact_public_product_and_cache_hit_still_guarded(cache):
    checks = []
    def guard():
        checks.append(True)
        return True
    detail = fetch(cache, guard)
    assert detail["fields"]["title"] == "Fixture"
    assert detail["product_url"] == URL
    assert detail["owned"] is False and detail["downloadable"] is False
    assert detail["download_url"] is None
    assert len(checks) == 3 and len(requests()) == 1
    assert fetch(cache, guard, title="  FIXTURE  ") == detail
    assert len(checks) == 5 and len(requests()) == 1
    with pytest.raises(ProviderCapabilityError, match="authority_revoked"):
        fetch(cache, lambda: False)
    assert len(requests()) == 1


@pytest.mark.parametrize("when", [1, 2, 3])
def test_revocation_before_cache_before_get_after_response(cache, when):
    checks = []
    def guard():
        checks.append(True)
        return len(checks) < when
    with pytest.raises(ProviderCapabilityError, match="authority_revoked"):
        fetch(cache, guard)
    assert len(checks) == when
    assert len(requests()) == int(when == 3)


@pytest.mark.parametrize("url", [
    URL + "?trackid=private", "https://other.example/123/book.ebook",
    "https://www.numilog.com/Pages/Readzis/Recommand.ashx?LivreId=123",
    "https://www.numilog.com/Pages/Compte/MesLivres.aspx",
])
def test_no_unqualified_route(cache, url):
    with pytest.raises(ProviderCapabilityError, match="detail_unverified"):
        fetch(cache, lambda: True, url=url)
    assert AnonymousClient.requests == []


def test_title_conflict_does_not_search_or_promote_purchased_variant(cache):
    with pytest.raises(ProviderCapabilityError, match="title_conflict"):
        fetch(cache, lambda: True, title="Titre privé différent")
    assert len(requests()) == 1
    assert fetch(cache, lambda: True)["fields"]["title"] == "Fixture"
    assert len(requests()) == 1


def test_429_shared_cache_cooldown_and_no_fallback(monkeypatch):
    class Limited(AnonymousClient):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.status = 429
        @property
        def headers(self):
            return {"retry-after": "120", "content-type": "text/html"}
    Limited.requests = []
    monkeypatch.setattr(client.HTTPX_TRANSPORT, "client", Limited)
    cache = PublicCatalogueCache(clock=lambda: 100)
    with pytest.raises(ProviderError, match="public_detail_rate_limited"):
        fetch(cache, lambda: True)
    with pytest.raises(ProviderError, match="public_catalog_rate_limited"):
        cache.get(("search", "unrelated"), lambda: pytest.fail("cooldown bypass"))
    assert len([item for item in Limited.requests if item[0] == "request"]) == 1
