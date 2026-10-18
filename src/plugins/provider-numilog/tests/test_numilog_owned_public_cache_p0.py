"""P0 #1072: anonymous public enrichment cache; owned entitlement is never cached.

These tests use a sanitized public fixture and synthetic product JSON-LD.
No real Numilog login, HTTP request, signed readBook URL or ACSM fulfillment.
"""
import json
from pathlib import Path

import pytest

from cifereca.plugin_api import ProviderError
from cifereca_numilog import NumilogSession
from cifereca_numilog_public import parse_public_search
from cifereca_numilog_public_cache import PublicCatalogueCache
from cifereca_numilog_public_detail import parse_public_detail_jsonld
import cifereca_numilog_public_client as client


FIX = Path(__file__).parent / "fixtures"
GOLDEN = json.loads((FIX / "public_search_jefferson_20261016.expected.json").read_text())
PAGE = parse_public_search(
    (FIX / "public_search_jefferson_20261016.html").read_text(),
    GOLDEN["source_url"],
)
CARD = PAGE.items[0]
URL = CARD.friendly_url


def owned_row():
    return {"lid": "42", "variant": "()", "title": CARD.title,
            "cover": None, "public_url": URL}


def session():
    instance = NumilogSession(
        credential_id="fictional-alice", label="Numilog", credentials={},
        options={}, config={}, diagnostics=None,
    )
    instance.connected = True
    return instance


def synthetic_public_detail():
    node = {"@context": "https://schema.org", "@type": "Book", "url": URL,
            "name": CARD.title, "author": {"name": "Synthetic Author"},
            "description": "A purely synthetic public description"}
    html = '<script type="application/ld+json">' + json.dumps(node) + '</script>'
    return parse_public_detail_jsonld(html, URL)


def test_repeated_explicit_listing_uses_one_public_get_but_rechecks_private_rights(monkeypatch):
    s = session()
    private, public = [], []
    active = [owned_row()]

    def fresh_inventory(**kwargs):
        private.append(kwargs)
        return list(active)

    s._rows = fresh_inventory
    monkeypatch.setattr(
        client, "fetch_public_search",
        lambda query, *, page, field:
        public.append((query, page, field)) or PAGE,
    )
    try:
        target = s._normalize(owned_row())
        first = s.enrich_owned_public_listing(target)
        second = s.enrich_owned_public_listing(target)
        assert first.metadata["public_enrichment"]["status"] == "attested_listing_match"
        assert second.owned is True and second.download_url is None
        assert len(private) == 2
        assert public == [(CARD.title, 0, "all")]

        # No positive private-rights cache is allowed even during public TTL.
        active.clear()
        with pytest.raises(ProviderError, match="non attestée"):
            s.enrich_owned_public_listing(target)
        assert len(private) == 3
        assert len(public) == 1
    finally:
        s.close()


def test_detail_reuses_only_anonymous_search_and_product_response(monkeypatch):
    s = session()
    s._rows = lambda **kwargs: [owned_row()]
    calls = []
    monkeypatch.setattr(
        client, "fetch_public_search",
        lambda query, *, page, field: calls.append("search") or PAGE,
    )
    monkeypatch.setattr(
        client, "fetch_public_detail",
        lambda url: calls.append("detail") or synthetic_public_detail(),
    )
    try:
        target = s._normalize(owned_row())
        for _ in range(2):
            result = s.enrich_owned_public_detail(target)
            assert result.metadata["public_detail_enrichment"]["status"] == "attested_public_product"
            assert result.owned is True and result.source_credential == "fictional-alice"
            assert result.download_url is None
        assert calls == ["search", "detail"]
        assert not hasattr(s._owned_public_cache, "credentials")
    finally:
        s.close()


def test_anonymous_budget_survives_invalidation_and_never_retries(monkeypatch):
    now = [100.0]
    s = session()
    s._owned_public_cache = PublicCatalogueCache(clock=lambda: now[0])
    s._rows = lambda **kwargs: [owned_row()]
    seen = []
    monkeypatch.setattr(
        client, "fetch_public_search",
        lambda query, *, page, field: seen.append("GET") or PAGE,
    )
    try:
        target = s._normalize(owned_row())
        s.enrich_owned_public_listing(target)
        s._owned_public_cache.clear()
        s.enrich_owned_public_listing(target)
        s._owned_public_cache.clear()
        with pytest.raises(ProviderError, match="request_budget_exhausted"):
            s.enrich_owned_public_listing(target)
        assert seen == ["GET", "GET"]
        now[0] += 1.0
        s.enrich_owned_public_listing(target)
        assert seen == ["GET", "GET", "GET"]
    finally:
        s.close()


def test_public_cache_not_shared_between_numilog_accounts(monkeypatch):
    sessions = [session(), session()]
    sessions[1].credential_id = "fictional-bob"
    visits = []
    monkeypatch.setattr(
        client, "fetch_public_search",
        lambda query, *, page, field: visits.append(query) or PAGE,
    )
    try:
        for s in sessions:
            s._rows = lambda **kwargs: [owned_row()]
            s.enrich_owned_public_listing(s._normalize(owned_row()))
        assert visits == [CARD.title, CARD.title]
    finally:
        for s in sessions:
            s.close()
