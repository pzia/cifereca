"""#1886: a source pager is evidence that a local filter is NOT exhaustive."""
from dataclasses import replace
from pathlib import Path

import pytest

from cifereca.plugin_api import ProviderCapabilityError
from cifereca_numilog_public import parse_public_search
from cifereca_numilog_public_local_filter import refine_loaded_public_page

URL = (
    "https://www.numilog.com/Pages/Recherche/ResultatRecherche.aspx"
    "?mode=brSimple&query=jefferson"
)
HTML = (
    Path(__file__).with_name("fixtures")
    / "public_search_jefferson_20261016.html"
).read_text()


def page():
    p = parse_public_search(HTML, URL)
    return replace(
        p, items=tuple(replace(r, source_provider="numilog-catalog")
                       for r in p.items)
    )


def test_forward_pager_prevents_forced_complete_flag():
    p = page()
    assert p.pagination_links
    fake = replace(
        p, total_results=len(p.items), complete=True, next_url=None,
    )
    with pytest.raises(ProviderCapabilityError, match="unverified_local_page"):
        refine_loaded_public_page(fake, author="unknown")


@pytest.mark.parametrize("links", [
    ({"url": "https://www.numilog.com/Pages/Recherche/ResultatRecherche.aspx?mode=brSimple&query=jefferson&GRID_PAGE=1"},),
    ({"url": "https://www.numilog.com/Pages/Recherche/ResultatRecherche.aspx?mode=brSimple&query=another"},),
    ({"url": "https://www.numilog.com/Pages/Recherche/ResultatRecherche.aspx?mode=brSimple&query=jefferson&token=PRIVATE"},),
    ({"url": 1},),
    ({"broken": "link"},),
])
def test_complete_page_refuses_unverified_or_other_search_pagers(links):
    p = page()
    fake = replace(
        p, items=p.items[:1], total_results=1, complete=True,
        next_url=None, pagination_links=links,
    )
    with pytest.raises(ProviderCapabilityError, match="unverified_local_page"):
        refine_loaded_public_page(fake, title="definitely-missing")


def test_complete_page_with_current_page_only_link_remains_usable():
    p = page()
    fake = replace(
        p, items=p.items[:1], total_results=1, complete=True,
        next_url=None, pagination_links=({"url": URL},),
    )
    result = refine_loaded_public_page(fake, author="definitely-missing")
    assert result.matching_count == 0
    assert result.public_page_complete is True
    assert result.has_uninspected_or_unverified_matches is False
