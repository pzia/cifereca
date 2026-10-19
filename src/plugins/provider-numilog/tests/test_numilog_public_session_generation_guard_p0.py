"""P0 #1886: generic catalogue search and detail cannot outlive a session.

Entirely offline: recorded anonymous search fixture and synthetic JSON-LD,
no personal purchases, network, login, cookies, signed URLs or ACSM.
"""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pytest

from cifereca.plugin_api import ProviderError
from cifereca_numilog_public import parse_public_search
from cifereca_numilog_public_detail import parse_public_detail_jsonld
import cifereca_numilog_public_client as client
import cifereca_numilog_public_provider as provider
import cifereca_numilog_public_detail as detail_module


SEARCH_URL = (
    "https://www.numilog.com/Pages/Recherche/ResultatRecherche.aspx"
    "?mode=brSimple&query=jefferson"
)
FIXTURE = Path(__file__).parent / "fixtures" / "public_search_jefferson_20261016.html"
PAGE = parse_public_search(FIXTURE.read_text(), SEARCH_URL)


def make_session():
    s = provider.PLUGIN.create_session(
        credential_id="__public__", label="Catalogue public",
        credentials={}, options={}, config={}, storage=None,
        diagnostics=None,
    )
    s.connect()
    return s


def product(item):
    node = {
        "@context": "https://schema.org", "@type": "Book",
        "url": item.friendly_url, "name": item.title,
        "description": "Résumé public attesté uniquement",
    }
    html = ('<script type="application/ld+json">'
            + json.dumps(node, ensure_ascii=False) + '</script>')
    return parse_public_detail_jsonld(html, item.friendly_url)


@pytest.mark.parametrize("transition", ["close", "reconnect"])
def test_generic_search_drops_rows_if_session_changes_during_projection(
    monkeypatch, transition,
):
    s = make_session()
    s._get_page = lambda *_args: PAGE
    original = s._public_rows
    calls = []

    def convert(items):
        rows = original(items)
        calls.append("projected")
        s.close()
        if transition == "reconnect":
            s.connect()
        return rows

    monkeypatch.setattr(s, "_public_rows", convert)
    try:
        with pytest.raises(ProviderError, match="navigation_invalidated") as caught:
            s.search("jefferson")
        assert calls == ["projected"]
        assert "jefferson" not in str(caught.value)
        assert "https://" not in str(caught.value)
    finally:
        s.close()


@pytest.mark.parametrize("transition", ["close", "reconnect"])
def test_generic_search_drops_rows_if_session_changes_during_page_lookup(
    monkeypatch, transition,
):
    s = make_session()
    fetched = []

    def load(*_args):
        fetched.append("loaded")
        s.close()
        if transition == "reconnect":
            s.connect()
        return PAGE

    monkeypatch.setattr(s, "_get_page", load)
    try:
        with pytest.raises(ProviderError, match="navigation_invalidated"):
            s.search("jefferson")
        assert fetched == ["loaded"]
    finally:
        s.close()


@pytest.mark.parametrize("transition", ["close", "reconnect"])
def test_explicit_detail_drops_result_if_session_changes_after_merge(
    monkeypatch, transition,
):
    s = make_session()
    item = replace(PAGE.items[0], source_provider="numilog-catalog")
    expected_detail = product(item)
    seen = []
    monkeypatch.setattr(client, "fetch_public_detail",
                        lambda *_args: seen.append("get") or expected_detail)
    original_merge = detail_module.merge_public_detail

    def merge(source, observed):
        result = original_merge(source, observed)
        seen.append("merged")
        s.close()
        if transition == "reconnect":
            s.connect()
        return result

    monkeypatch.setattr(detail_module, "merge_public_detail", merge)
    try:
        with pytest.raises(ProviderError, match="navigation_invalidated") as caught:
            s.enrich_public_detail(item)
        assert seen == ["get", "merged"]
        assert "https://" not in str(caught.value)
        assert item.title not in str(caught.value)
    finally:
        s.close()


@pytest.mark.parametrize("transition", ["close", "reconnect"])
def test_explicit_detail_drops_result_if_session_changes_on_cached_hit(
    monkeypatch, transition,
):
    s = make_session()
    item = replace(PAGE.items[0], source_provider="numilog-catalog")
    observed = product(item)
    fetches = []
    monkeypatch.setattr(client, "fetch_public_detail",
                        lambda *_args: fetches.append("fetch") or observed)
    try:
        first = s.enrich_public_detail(item)
        assert first.owned is False and first.downloadable is False
        assert fetches == ["fetch"]
        original_merge = detail_module.merge_public_detail

        def late_merge(source, record):
            result = original_merge(source, record)
            s.close()
            if transition == "reconnect":
                s.connect()
            return result

        monkeypatch.setattr(detail_module, "merge_public_detail", late_merge)
        with pytest.raises(ProviderError, match="navigation_invalidated"):
            s.enrich_public_detail(item)
        # The response was an actual cache hit; no second anonymous GET.
        assert fetches == ["fetch"]
    finally:
        s.close()


def test_unmodified_search_and_detail_still_work_without_extra_http(monkeypatch):
    s = make_session()
    item = replace(PAGE.items[0], source_provider="numilog-catalog")
    counters = []
    monkeypatch.setattr(provider, "fetch_public_search",
                        lambda *_args, **_kwargs:
                        counters.append("search") or PAGE)
    monkeypatch.setattr(client, "fetch_public_detail",
                        lambda *_args: counters.append("detail") or product(item))
    try:
        first = s.search("jefferson")
        second = s.search("jefferson")
        enriched = s.enrich_public_detail(first[0])
        assert len(first) == len(second) == len(PAGE.items)
        assert first[0].source_provider == "numilog-catalog"
        assert all(not x.owned and not x.downloadable and x.download_url is None
                   for x in first + second)
        assert enriched.record_id == first[0].record_id
        assert enriched.owned is False and enriched.download_url is None
        assert "public_detail" in enriched.metadata
        assert counters == ["search", "detail"]
    finally:
        s.close()
