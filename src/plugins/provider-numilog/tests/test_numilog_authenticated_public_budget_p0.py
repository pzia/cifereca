"""P0 #1886: private Numilog session's PUBLIC APIs share one anonymous budget.

Offline fixture and synthetic HTTP failures only. No login, cookies, account
requests, real product GET, downloads, ACSM or ownership inference.
"""
from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from cifereca.plugin_api import ProviderCapabilityError, ProviderError
from cifereca_numilog import NumilogSession
from cifereca_numilog_public import parse_public_search
from cifereca_numilog_public_cache import PublicCatalogueCache
import cifereca_numilog_public_client as client


FIX = Path(__file__).parent / "fixtures"
EXPECTED = json.loads((FIX / "public_search_jefferson_20261016.expected.json").read_text())
PAGE = parse_public_search(
    (FIX / "public_search_jefferson_20261016.html").read_text(),
    EXPECTED["source_url"],
)
CARD = PAGE.items[0]
PRODUCT = CARD.friendly_url


def session(account="fictional"):
    s = NumilogSession(
        credential_id=account, label="Numilog",
        credentials={}, options={}, config={}, diagnostics=None,
    )
    s.connected = True  # offline: no account handshake or HTTP
    return s


def test_public_search_and_catalog_scope_reuse_same_cached_page(monkeypatch):
    s = session()
    calls = []
    def fake_fetch(query, *, page, field):
        calls.append((query, page, field))
        return PAGE
    monkeypatch.setattr(client, "fetch_public_search", fake_fetch)
    s._rows = lambda **kwargs: pytest.fail("public catalogue must not read Mes achats")
    try:
        first = s.search_public("jefferson")
        first[0].metadata["secret"] = "private mutation"
        second = s.search("jefferson", scope="catalog")
        assert len(first) == len(second) == 20
        assert "secret" not in second[0].metadata
        assert calls == [("jefferson", 0, "all")]
        assert all(not r.owned and not r.downloadable and r.download_url is None
                   and r.source_credential == "__public__" for r in second)
    finally:
        s.close()


def test_search_and_product_detail_share_429_cooldown_and_public_cache(monkeypatch):
    now = [100.0]
    s = session()
    s._owned_public_cache = PublicCatalogueCache(clock=lambda: now[0])
    calls = []
    monkeypatch.setattr(client, "fetch_public_search",
                        lambda query, *, page, field: calls.append(("search", query)) or PAGE)

    def rate_limited(_url):
        calls.append(("detail", "rate_limited"))
        raise client.PublicRateLimitError("Numilog : public_detail_rate_limited", "120")
    monkeypatch.setattr(client, "fetch_public_detail", rate_limited)
    try:
        s.search_public("jefferson")
        with pytest.raises(ProviderError, match="public_detail_rate_limited"):
            s.public_product_detail(PRODUCT)
        with pytest.raises(ProviderError, match="public_catalog_rate_limited"):
            s.search_public("different-public-query")
        with pytest.raises(ProviderError, match="public_catalog_rate_limited"):
            s.public_product_detail("https://www.numilog.com/999999/test.ebook")
        assert s.search_public("jefferson")  # cached page: no HTTP
        assert calls == [("search", "jefferson"), ("detail", "rate_limited")]
        # Reconnect/clear is not an escape from the server's retry-after.
        s._owned_public_cache.clear()
        with pytest.raises(ProviderError, match="public_catalog_rate_limited"):
            s.search_public("jefferson")
        assert len(calls) == 2
        now[0] += 121
        assert len(s.search_public("jefferson")) == 20
        assert calls == [
            ("search", "jefferson"), ("detail", "rate_limited"),
            ("search", "jefferson"),
        ]
    finally:
        s.close()


def test_direct_public_product_reads_are_cached_defensively(monkeypatch):
    s = session()
    calls = []
    def public_detail(url):
        calls.append(url)
        return {"owned": False, "book": {"title": "Public only"}, "license": None}
    monkeypatch.setattr(client, "fetch_public_detail", public_detail)
    try:
        first = s.public_product_detail(PRODUCT)
        first["book"]["title"] = "changed"
        second = s.public_product_detail(PRODUCT)
        assert second["book"]["title"] == "Public only"
        assert calls == [PRODUCT]
        assert "credentials" not in str(second)
    finally:
        s.close()


def test_explicit_owned_enrichment_reuses_same_anonymous_search(monkeypatch):
    s = session()
    s._rows = lambda **kwargs: [
        {"lid": "42", "variant": "()", "title": CARD.title,
         "cover": None, "public_url": PRODUCT},
    ]
    calls = []
    monkeypatch.setattr(client, "fetch_public_search",
                        lambda query, *, page, field:
                        calls.append((query, page, field)) or PAGE)
    try:
        s.search_public(CARD.title)
        result = s.enrich_owned_public_listing(s._normalize(s._rows()[0]))
        assert result.metadata["public_enrichment"]["status"] == "attested_listing_match"
        assert result.owned is True and result.download_url is None
        assert calls == [(CARD.title, 0, "all")]
    finally:
        s.close()


@pytest.mark.parametrize("input_value", [
    None, True, 42, [], {}, "https://www.numilog.com/Pages/Compte/MesLivres.aspx",
    "https://www.numilog.com/1171334/book.ebook?token=PRIVATE",
    "https://another.invalid/12345/book.ebook",
])
def test_unqualified_detail_url_is_rejected_before_cache_or_http(monkeypatch, input_value):
    s = session()
    calls = []
    monkeypatch.setattr(client, "fetch_public_detail", lambda url: calls.append(url))
    try:
        with pytest.raises(ProviderCapabilityError, match="invalid_public_product_url"):
            s.public_product_detail(input_value)
        assert not calls
    finally:
        s.close()


@pytest.mark.parametrize("kwargs", [
    {"field": "author"}, {"field": "isbn"}, {"field": True},
    {"page": True}, {"page": -1}, {"page": 101},
    {"query": "a"}, {"query": None}, {"query": "token=PRIVATE" + chr(10)},
])
def test_invalid_public_search_is_rejected_before_cache_or_http(monkeypatch, kwargs):
    s = session()
    calls = []
    monkeypatch.setattr(client, "fetch_public_search",
                        lambda *args, **kw: calls.append((args, kw)))
    try:
        params = {"query": "jefferson", "field": "all", "page": 0}
        params.update(kwargs)
        with pytest.raises(ProviderCapabilityError):
            s.search_public(**params)
        assert not calls
    finally:
        s.close()


def test_anonymous_cache_is_per_account_but_has_no_account_payload(monkeypatch):
    sessions = (session("fictional-a"), session("fictional-b"))
    calls = []
    monkeypatch.setattr(client, "fetch_public_search",
                        lambda query, *, page, field:
                        calls.append("anonymous-only") or PAGE)
    try:
        for s in sessions:
            assert len(s.search_public("jefferson")) == 20
        assert calls == ["anonymous-only", "anonymous-only"]
        assert all(not hasattr(s._owned_public_cache, "credentials") for s in sessions)
    finally:
        for s in sessions:
            s.close()


def test_search_in_flight_blocks_detail_and_is_invalidated_on_disconnect(monkeypatch):
    """Switching public entrypoints cannot create a second concurrent GET."""
    s = session()
    entered, release = threading.Event(), threading.Event()
    fetched = []

    def blocked_search(query, *, page, field):
        fetched.append("search")
        entered.set()
        assert release.wait(5)
        return PAGE

    monkeypatch.setattr(client, "fetch_public_search", blocked_search)
    monkeypatch.setattr(client, "fetch_public_detail",
                        lambda url: fetched.append("detail") or {"unreachable": True})
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(s.search_public, "jefferson")
            assert entered.wait(5)
            with pytest.raises(ProviderError, match="request_in_progress"):
                s.public_product_detail(PRODUCT)
            assert fetched == ["search"]
            # A close never waits for a network call and prevents its stale
            # result from reviving an old account's public search view.
            s.close()
            release.set()
            with pytest.raises(ProviderError, match="request_invalidated"):
                pending.result(timeout=5)
        assert not s._owned_public_cache._entries
    finally:
        release.set()
        s.close()
