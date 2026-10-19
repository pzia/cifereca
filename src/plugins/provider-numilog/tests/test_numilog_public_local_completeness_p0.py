"""P0 #1886: never promote a partial anonymous listing to an exhaustive filter.

Synthetic modifications to one offline Numilog HTML search fixture. No HTTP,
account access, purchase, private files, or DRM artifacts.
"""
from dataclasses import replace
from pathlib import Path

import pytest

from cifereca.plugin_api import ProviderCapabilityError
from cifereca_numilog_public import parse_public_search
from cifereca_numilog_public_local_filter import refine_loaded_public_page

URL = "https://www.numilog.com/Pages/Recherche/ResultatRecherche.aspx?mode=brSimple&query=jefferson"
HTML = (Path(__file__).parent / "fixtures" / "public_search_jefferson_20261016.html").read_text()


def observed_page():
    parsed = parse_public_search(HTML, URL)
    return replace(parsed, items=tuple(replace(row, source_provider="numilog-catalog")
                                       for row in parsed.items))


@pytest.mark.parametrize("mutation", [
    # A catalogue declaring 242 books cannot have finished after 20.
    lambda p: replace(p, complete=True),
    # Even a manipulated total of 20 cannot prove completion while the
    # upstream paginator still attests an additional page.
    lambda p: replace(p, complete=True, total_results=len(p.items)),
    # A page other than the first cannot establish an exhaustive catalogue.
    lambda p: replace(p, complete=True, page_index=1),
    lambda p: replace(p, complete=True, next_url=""),
])
def test_mutated_completion_rejected_before_exhaustive_negative(mutation):
    page = mutation(observed_page())
    with pytest.raises(ProviderCapabilityError, match="unverified_local_page"):
        refine_loaded_public_page(page, author="not-a-book-in-this-fixture")


def test_ordinary_partial_page_remains_explicitly_nonexhaustive():
    result = refine_loaded_public_page(
        observed_page(), title="not-a-book-in-this-fixture",
    )
    assert result.matching_count == 0
    assert result.missing_evidence_count == 0
    assert result.public_unfiltered_total == 242
    assert result.loaded_count == 20
    assert result.public_page_index == 0
    assert result.public_page_complete is False
    assert result.has_uninspected_or_unverified_matches is True


def test_coherent_single_page_does_not_invent_more_results():
    observed = observed_page()
    # A controlled, consistent one-item page is intentionally synthetic;
    # no claim is made that the live Numilog query has this result set.
    synthetic = replace(
        observed, items=observed.items[:1], total_results=1,
        complete=True, next_url=None, pagination_links=(),
    )
    result = refine_loaded_public_page(synthetic, title="not-in-page")
    assert result.public_page_complete is True
    assert result.public_unfiltered_total == 1
    assert result.matching_count == 0
    assert result.has_uninspected_or_unverified_matches is False


def test_explicit_complete_flag_false_is_conservative_not_a_false_negative():
    observed = observed_page()
    synthetic = replace(
        observed, items=observed.items[:1], total_results=1,
        complete=False, next_url=None, pagination_links=(),
    )
    result = refine_loaded_public_page(synthetic, title="not-in-page")
    assert result.public_page_complete is False
    assert result.has_uninspected_or_unverified_matches is True
