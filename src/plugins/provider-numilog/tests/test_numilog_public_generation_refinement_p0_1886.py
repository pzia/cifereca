"""P0 #1886: stale local refinement/selection is rejected across session changes.

Only observed public HTML fixtures are used. No HTTP, owned rights, credentials,
signed download or purchase is involved.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from cifereca.plugin_api import ProviderError
from cifereca_numilog_public import parse_public_search
import cifereca_numilog_public_provider as provider

FIXTURE = Path(__file__).parent / "fixtures" / "public_search_jefferson_20261016.html"
URL = ("https://www.numilog.com/Pages/Recherche/ResultatRecherche.aspx"
       "?mode=brSimple&query=jefferson")
PAGE = parse_public_search(FIXTURE.read_text(encoding="utf-8"), URL)


def session():
    s = provider.PLUGIN.create_session(
        credential_id="__public__", label="Public",
        credentials={}, options={}, config={}, storage=None, diagnostics=None,
    )
    s.connect()
    return s


@pytest.mark.parametrize("method,module_name,function_name", [
    ("refine_loaded_page", "cifereca_numilog_public_local_filter",
     "refine_loaded_public_page"),
    ("refine_loaded_pages", "cifereca_numilog_public_multi_refinement",
     "refine_loaded_public_pages"),
])
@pytest.mark.parametrize("transition", ["close", "reconnect"])
def test_refinement_rejects_generation_change_during_offline_filter(
    monkeypatch, method, module_name, function_name, transition,
):
    import importlib
    module = importlib.import_module(module_name)
    original = getattr(module, function_name)
    s = session()
    calls = []

    def interrupted(*args, **kwargs):
        value = original(*args, **kwargs)
        calls.append("offline")
        s.close()
        if transition == "reconnect":
            s.connect()
        return value

    monkeypatch.setattr(module, function_name, interrupted)
    try:
        args = ([PAGE] if method == "refine_loaded_pages" else PAGE,)
        with pytest.raises(ProviderError, match="navigation_invalidated"):
            getattr(s, method)(*args, author="Mourlevat")
        assert calls == ["offline"]
        with s._navigation_lock:
            assert not s._navigation_urls
    finally:
        s.close()


@pytest.mark.parametrize("transition", ["close", "reconnect"])
def test_product_selection_rejects_reconnect_during_validation_before_get(
    monkeypatch, transition,
):
    import cifereca_numilog_public_local_filter as local_filter
    checked = local_filter._validate_page
    s = session()
    fetches = []

    def interrupted(page):
        checked(page)
        s.close()
        if transition == "reconnect":
            s.connect()

    monkeypatch.setattr(local_filter, "_validate_page", interrupted)
    monkeypatch.setattr(provider, "fetch_public_search_url",
                        lambda *_args: fetches.append("search") or PAGE)
    import cifereca_numilog_public_client as public_client
    monkeypatch.setattr(public_client, "fetch_public_detail",
                        lambda *_args: fetches.append("product") or {})
    try:
        with pytest.raises(ProviderError, match="navigation_invalidated"):
            s.enrich_loaded_page_product(PAGE, PAGE.items[0].record_id)
        assert fetches == []
    finally:
        s.close()


def test_unchanged_generation_keeps_offline_page_refinement(monkeypatch):
    s = session()
    try:
        refined = s.refine_loaded_page(PAGE, author="Mourlevat")
        assert refined.scope == "loaded_public_page_only"
        assert refined.matching_count >= 1
        assert all(not item.owned and not item.downloadable
                   and item.download_url is None for item in refined.items)
    finally:
        s.close()
