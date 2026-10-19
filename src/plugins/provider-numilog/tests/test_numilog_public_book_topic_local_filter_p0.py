"""#1886 P0: local topic/series filters require an explicitly enriched Book.

Search cards are recorded public HTML; JSON-LD details are synthetic.
Filtering never opens HTTP, fetches missing details or implies ownership.
"""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path

import pytest

from cifereca.plugin_api import ProviderCapabilityError
from cifereca_numilog_public import parse_public_search
from cifereca_numilog_public_detail import merge_public_detail, parse_public_detail_jsonld
from cifereca_numilog_public_local_filter import refine_loaded_public_page

HERE = Path(__file__).parent / "fixtures"
SOURCE = ("https://www.numilog.com/Pages/Recherche/ResultatRecherche.aspx"
          "?mode=brSimple&query=jefferson")
PAGE = parse_public_search((HERE / "public_search_jefferson_20261016.html").read_text(),
                           SOURCE)


def selected(index=0, *, enriched=False, genre="Roman jeunesse"):
    item = replace(PAGE.items[index], source_provider="numilog-catalog")
    if enriched:
        node = {
            "@context": "https://schema.org", "@type": "Book",
            "name": item.title, "url": item.friendly_url,
            "genre": ["Aventure", genre],
            "keywords": "forêt, enquête, amitié",
            "isPartOf": {
                "@type": "BookSeries",
                "name": "Les enquêtes de Jefferson",
            },
        }
        detail = parse_public_detail_jsonld(
            '<script type="application/ld+json">'
            + json.dumps(node, ensure_ascii=False) + "</script>",
            item.friendly_url,
        )
        item = merge_public_detail(item, detail)
    return item


def one(item):
    # A deliberately loaded subset never changes the server-issued total.
    return replace(PAGE, items=(item,))


@pytest.mark.parametrize("filters", [
    {"genre": "roman jeunesse"},
    {"genre": "AVENTURE"},
    {"series": "enquêtes de Jefferson"},
    {"keyword": "forêt"},
    {"keyword": "AMITIE"},
    {"genre": "aventure", "series": "Jefferson", "keyword": "enquête"},
])
def test_positive_topic_filters_use_single_attested_loaded_product(filters):
    item = selected(enriched=True)
    found = refine_loaded_public_page(one(item), **filters)
    assert found.matching_count == 1
    assert found.loaded_count == 1
    assert found.public_unfiltered_total == PAGE.total_results
    assert found.public_page_complete is False
    assert found.scope == "loaded_public_page_only"
    assert found.has_uninspected_or_unverified_matches is True
    assert found.applied_fields == tuple(sorted(filters))
    assert found.missing_evidence_count == 0
    assert found.items[0].record_id == item.record_id
    assert found.items[0].owned is False
    assert found.items[0].downloadable is False
    assert found.items[0].download_url is None


@pytest.mark.parametrize("filters", [
    {"genre": "aventure"},
    {"series": "Jefferson"},
    {"keyword": "forêt"},
])
def test_listing_without_explicit_detail_is_unknown_not_no_results(filters):
    page = one(selected())
    result = refine_loaded_public_page(page, **filters)
    assert result.matching_count == 0
    assert result.missing_evidence_count == 1
    assert result.has_uninspected_or_unverified_matches is True


@pytest.mark.parametrize("filters", [
    {"genre": "science-fiction"},
    {"series": "autre série"},
    {"keyword": "jardin"},
])
def test_other_published_values_do_not_prove_global_negative(filters):
    result = refine_loaded_public_page(one(selected(enriched=True)), **filters)
    assert result.matching_count == 0
    assert result.missing_evidence_count == 1
    assert result.has_uninspected_or_unverified_matches is True


def test_two_homonyms_are_distinct_public_products_and_only_one_was_enriched():
    assert PAGE.items[1].title == PAGE.items[2].title
    card_a = selected(1, enriched=True, genre="Roman policier")
    card_b = selected(2)
    page = replace(PAGE, items=(card_a, card_b))
    result = refine_loaded_public_page(page, genre="Roman policier")
    assert result.matching_count == 1
    assert result.items[0].record_id == card_a.record_id
    assert result.missing_evidence_count == 1
    assert result.public_unfiltered_total == PAGE.total_results
    assert card_a.friendly_url != card_b.friendly_url
    assert all(not row.owned for row in result.items)


@pytest.mark.parametrize("change", [
    "source", "detail_url", "title", "proof", "rights",
    "format", "field", "missing", "identifiers", "commercial", "visible",
])
def test_forged_cached_detail_never_creates_positive_matching_field(change):
    item = selected(enriched=True)
    metadata = deepcopy(item.metadata)
    d = metadata["public_detail"]
    if change == "source":
        d["source_url"] = PAGE.items[1].friendly_url
    elif change == "detail_url":
        d["edition_match"] = "pretend_exact_purchased_file"
    elif change == "title":
        d["fields"]["title"] = "Another Book"
    elif change == "proof":
        d["field_evidence"]["genres"]["source_url"] = PAGE.items[1].friendly_url
    elif change == "rights":
        d["rights_proven"] = True
    elif change == "format":
        d["format_edition_proven"] = True
    elif change == "field":
        d["fields"]["genres"] = ["PRIVATE\u202e"]
    elif change == "missing":
        del d["field_evidence"]["genres"]
    elif change == "identifiers":
        d["public_identifiers"] = {"epub": "9782075241939"}
    elif change == "commercial":
        d["public_commercial"] = {"download_url": "PRIVATE"}
    elif change == "visible":
        d["public_html_metadata"] = {
            "product_url": item.friendly_url,
            "fields": {"language_label": "PRIVATE"},
            "field_evidence": {},
        }
    row = replace(item, metadata=metadata)
    result = refine_loaded_public_page(one(row), genre="Roman jeunesse")
    assert result.matching_count == 0
    assert result.missing_evidence_count == 1
    assert result.has_uninspected_or_unverified_matches
    assert "PRIVATE" not in repr(result.items)


@pytest.mark.parametrize("filters", [
    {"genre": ""}, {"series": " "}, {"keyword": "??"},
    {"genre": "PRIVATE\n"}, {"series": "PRIVATE\u202e"},
    {"keyword": 42}, {"genre": ["Roman"]},
])
def test_malformed_topic_filter_refused_without_loading_page(filters):
    with pytest.raises(ProviderCapabilityError, match="invalid_local_filter"):
        refine_loaded_public_page(None, **filters)
