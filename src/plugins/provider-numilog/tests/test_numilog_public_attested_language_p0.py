"""#1886 P0: Numilog language is a POSITIVE-ONLY explicit detail-page filter.

One anonymized public search fixture, synthetic Book JSON-LD product details.
No live endpoint, account, HTTP, owned rights or automatic N+1 enrichment.
"""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from cifereca.plugin_api import ProviderCapabilityError
from cifereca_numilog_public import parse_public_search
from cifereca_numilog_public_detail import parse_public_detail_jsonld, merge_public_detail
from cifereca_numilog_public_local_filter import refine_loaded_public_page

SOURCE = "https://www.numilog.com/Pages/Recherche/ResultatRecherche.aspx?mode=brSimple&query=jefferson"
HTML = (Path(__file__).parent / "fixtures" / "public_search_jefferson_20261016.html").read_text()


def catalogue_page():
    page = parse_public_search(HTML, SOURCE)
    return replace(page, items=tuple(
        replace(item, source_provider="numilog-catalog") for item in page.items
    ))


def enriched_page(page, language="fr", *, modified_title=None):
    item = page.items[0]
    node = {
        "@context": "https://schema.org", "@type": "Book",
        "url": item.friendly_url, "name": item.title,
        "inLanguage": language,
    }
    if modified_title is not None:
        node["name"] = modified_title
    text = (
        '<html><form id="aspnetForm" name="aspnetForm" method="post" action="'
        + urlsplit(item.friendly_url).path + '"><h1>'
        + item.title + '</h1></form><script type="application/ld+json">'
        + json.dumps(node, ensure_ascii=False)
        + '</script></html>'
    )
    detail = parse_public_detail_jsonld(text, item.friendly_url)
    enriched = merge_public_detail(item, detail)
    return replace(page, items=(enriched, *page.items[1:]))


def test_plain_search_cards_do_not_claim_language_or_exhaustive_absence():
    page = catalogue_page()
    result = refine_loaded_public_page(page, language="fr")
    assert result.matching_count == 0
    assert result.missing_evidence_count == 20
    assert result.scope == "loaded_public_page_only"
    assert result.has_uninspected_or_unverified_matches is True
    assert result.public_unfiltered_total == 242


def test_one_explicit_public_book_language_is_a_positive_match_only():
    page = enriched_page(catalogue_page())
    french = refine_loaded_public_page(page, language="FR")
    assert french.matching_count == 1
    assert french.missing_evidence_count == 19
    assert french.items[0].title == "Jefferson prend des risques"
    assert french.applied_fields == ("language",)
    assert french.items[0].owned is False
    assert french.items[0].downloadable is False
    assert french.items[0].download_url is None

    # Published "fr" cannot prove that no other language is published.
    english = refine_loaded_public_page(page, language="en")
    assert english.matching_count == 0
    assert english.missing_evidence_count == 20
    assert english.has_uninspected_or_unverified_matches is True
    assert english.public_unfiltered_total == french.public_unfiltered_total


def test_incompatible_title_or_corrupt_language_proof_is_unknown():
    page = enriched_page(catalogue_page())
    first = page.items[0]
    replaced_title = replace(first, title="Autre édition")
    wrong = replace(page, items=(replaced_title, *page.items[1:]))
    assert refine_loaded_public_page(wrong, language="fr").matching_count == 0

    details = dict(first.metadata["public_detail"])
    original = dict(details["field_evidence"])
    original["language"] = {
        **original["language"], "source_url": "https://evil.invalid/?token=PRIVATE"
    }
    details["field_evidence"] = original
    bad = replace(first, metadata={**first.metadata, "public_detail": details})
    modified = replace(page, items=(bad, *page.items[1:]))
    result = refine_loaded_public_page(modified, language="fr")
    assert result.matching_count == 0 and result.missing_evidence_count == 20


def test_language_combines_with_attested_author_but_never_hits_network():
    page = enriched_page(catalogue_page())
    result = refine_loaded_public_page(
        page, language="fr", author="Jean-Claude Mourlevat",
    )
    assert result.matching_count == 1
    assert result.applied_fields == ("author", "language")
    assert result.loaded_count == 20
    assert result.has_uninspected_or_unverified_matches is True


@pytest.mark.parametrize("bad", ("", "?", "\u202ePRIVATE", "\n", "x"*121, True, 123, {}))
def test_invalid_language_filter_refused_before_any_scan(bad):
    with pytest.raises(ProviderCapabilityError, match="invalid_local_filter"):
        refine_loaded_public_page(catalogue_page(), language=bad)
