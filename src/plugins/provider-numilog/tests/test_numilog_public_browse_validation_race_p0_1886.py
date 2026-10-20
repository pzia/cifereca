"""P0 #1886: explicit public browse refuses a stale session during validation.

No authentication, real network, signed link, purchase or fulfillment.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from cifereca.plugin_api import ProviderError
import cifereca_numilog_public_provider as provider
from cifereca_numilog_public import parse_public_search

FIXTURE = Path(__file__).parent / "fixtures" / "public_search_jefferson_20261016.html"
SEARCH_URL = (
    "https://www.numilog.com/Pages/Recherche/ResultatRecherche.aspx"
    "?mode=brSimple&query=jefferson"
)
PAGE = parse_public_search(FIXTURE.read_text(), SEARCH_URL)


def session():
    result = provider.PLUGIN.create_session(
        credential_id="__public__", label="Catalogue",
        credentials={}, options={}, config={}, storage=None, diagnostics=None,
    )
    result.connect()
    return result


@pytest.mark.parametrize("transition", ["close", "reconnect"])
def test_browse_rejects_session_change_while_validating_before_any_fetch(
    monkeypatch, transition,
):
    s = session()
    checked = s._validate_search
    network = []

    def validate(query, page, field, scope):
        url = checked(query, page, field, scope)
        s.close()
        if transition == "reconnect":
            s.connect()
        return url

    monkeypatch.setattr(s, "_validate_search", validate)
    monkeypatch.setattr(s, "_get_page",
                        lambda *_args: network.append("stale-public-fetch") or PAGE)
    try:
        with pytest.raises(ProviderError, match="navigation_invalidated") as exc:
            s.browse_public_search("jefferson")
        assert network == []
        assert "jefferson" not in str(exc.value)
        assert "https://" not in str(exc.value)
        with s._navigation_lock:
            assert not s._navigation_urls
    finally:
        s.close()


def test_browse_still_reuses_public_cache_without_redundant_fetch(monkeypatch):
    calls = []
    monkeypatch.setattr(provider, "fetch_public_search",
                        lambda *_args, **_kw: calls.append("anon-fetch") or PAGE)
    s = session()
    try:
        first = s.browse_public_search("jefferson")
        second = s.browse_public_search("jefferson")
        assert calls == ["anon-fetch"]
        assert first.next_url == second.next_url
        assert all(row.owned is False and row.downloadable is False
                   and row.download_url is None for row in second.items)
    finally:
        s.close()
