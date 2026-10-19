"""#1886 P0: local Numilog filters cannot pretend punctuation or mixed pages match.

Only offline, previously observed anonymous search fixtures; no credentials,
real HTTP, ownership, loan claims, library download, or ACSM access.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from cifereca.plugin_api import ProviderCapabilityError
from cifereca_numilog_public import parse_public_search
from cifereca_numilog_public_local_filter import refine_loaded_public_page

HTML = (Path(__file__).parent / "fixtures" / "public_search_jefferson_20261016.html").read_text()
URL = "https://www.numilog.com/Pages/Recherche/ResultatRecherche.aspx?mode=brSimple&query=jefferson"


def page():
    p = parse_public_search(HTML, URL)
    return replace(
        p, items=tuple(replace(row, source_provider="numilog-catalog") for row in p.items)
    )


@pytest.mark.parametrize("field", ["title", "author", "publisher", "format_label"])
@pytest.mark.parametrize("query", ["!!!", "---", "…", "£€$", "  +  = ", "\u0301"])
def test_punctuation_or_combining_only_filter_must_not_match_every_row(field, query):
    with pytest.raises(ProviderCapabilityError, match="invalid_local_filter") as error:
        refine_loaded_public_page(page(), **{field: query})
    assert "jefferson" not in str(error.value)


@pytest.mark.parametrize("value", ["JeFfErSoN", "Jean-Claude", "école"])
def test_meaningful_normalized_filter_remains_usable(value):
    result = refine_loaded_public_page(page(), title=value)
    assert result.scope == "loaded_public_page_only"
    assert result.loaded_count == 20
    assert result.public_unfiltered_total == 242


def replace_metadata(row, **changes):
    return replace(row, metadata={**row.metadata, **changes})


@pytest.mark.parametrize("mutate", [
    lambda p: replace(p, items=(p.items[0], p.items[0])),
    lambda p: replace(p, items=(p.items[0], replace_metadata(
        p.items[1], source_url=URL.replace("jefferson", "another")))),
    lambda p: replace(p, items=(replace_metadata(
        p.items[0], source_url=URL.replace("jefferson", "another")), *p.items[1:])),
    lambda p: replace(p, items=(replace_metadata(
        p.items[0], page_index=1), *p.items[1:])),
    lambda p: replace(p, items=(replace_metadata(
        p.items[0], source_item_id="9999999"), *p.items[1:])),
    lambda p: replace(p, items=(replace(
        p.items[0], record_id="numilog:public:other"), *p.items[1:])),
    lambda p: replace(p, page_index=1),
    lambda p: replace(p, page_index=True),
    lambda p: replace(p, total_results=True),
    lambda p: replace(p, complete="true"),
    lambda p: replace(p, total_results=19),
])
def test_mixed_pages_or_unqualified_result_counters_are_not_proven(mutate):
    forged = mutate(page())
    with pytest.raises(ProviderCapabilityError, match="unverified_local_page"):
        refine_loaded_public_page(forged, author="Mourlevat")


def test_positive_refinement_preserves_page_coverage_and_identity():
    p = page()
    result = refine_loaded_public_page(p, author="Mourlevat")
    assert result.loaded_count == len(p.items)
    assert result.public_unfiltered_total == p.total_results
    assert result.public_page_index == 0
    assert result.public_page_complete is False
    assert result.has_uninspected_or_unverified_matches is True
    assert all(row.source_provider == "numilog-catalog"
               and row.source_collection == "public_catalog"
               and row.owned is False and row.downloadable is False
               for row in result.items)
