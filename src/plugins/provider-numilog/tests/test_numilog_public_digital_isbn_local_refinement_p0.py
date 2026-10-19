"""#1886 P0: digitized ISBN filter works ONLY with explicitly attested detail.

No network, credentials, acquisition or synthetic claim of a real Numilog
search-by-ISBN endpoint. Listing and canonical URLs are from a recorded
anonymous search; labelled EAN details in this fixture are *synthetic*.
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
PAPER = "9782092591086"
EPUB = "9782092591093"
PDF = "9782380354539"


def listing():
    observed = parse_public_search(HTML, SOURCE)
    return replace(observed, items=tuple(
        replace(row, source_provider="numilog-catalog") for row in observed.items
    ))


def synthetic_detail(item, *, visible="", json_isbn=PAPER, title=None):
    name = title if title is not None else item.title
    node = {"@context": "https://schema.org", "@type": "Book",
            "url": item.friendly_url, "name": name, "isbn": json_isbn}
    html = (
        '<html><form id="aspnetForm" name="aspnetForm" method="post" action="'
        + urlsplit(item.friendly_url).path + '"><h1>'
        + name + '</h1><h2>Détails</h2>' + visible
        + '</form><script type="application/ld+json">'
        + json.dumps(node, ensure_ascii=False)
        + '</script></html>'
    )
    return parse_public_detail_jsonld(html, item.friendly_url)


def with_explicit_detail(page, *, visible="", json_isbn=PAPER):
    first = page.items[0]
    detail = synthetic_detail(first, visible=visible, json_isbn=json_isbn)
    enriched = merge_public_detail(first, detail)
    # No background N+1: this test inserts ONE previously requested detail
    # into the already-loaded public page, leaving other cards untouched.
    return replace(page, items=(enriched, *page.items[1:]))


def assert_partial_unknown(result, *, matches, unknown):
    assert result.scope == "loaded_public_page_only"
    assert result.applied_fields == ("isbn_digital",)
    assert result.matching_count == matches
    assert result.missing_evidence_count == unknown
    assert result.public_unfiltered_total == 242
    assert result.loaded_count == 20
    assert result.has_uninspected_or_unverified_matches is True
    assert all(not row.owned and not row.downloadable
               and row.download_url is None
               for row in result.items)


def test_public_listing_alone_never_guesses_digital_isbn():
    page = listing()
    result = refine_loaded_public_page(page, isbn_digital=EPUB)
    assert_partial_unknown(result, matches=0, unknown=20)


def test_epub_label_enables_exact_positive_match_without_claiming_negative():
    page = with_explicit_detail(
        listing(), visible=(
            "<dt>EAN papier</dt><dd>" + PAPER + "</dd>"
            "<dt>EAN EPUB SANS DRM</dt><dd>" + EPUB + "</dd>"
        ),
    )
    epub = refine_loaded_public_page(page, isbn_digital=EPUB)
    assert_partial_unknown(epub, matches=1, unknown=19)
    assert epub.items[0].title == "Jefferson prend des risques"
    assert epub.items[0].metadata["public_detail"]["public_identifiers"] == {
        "paper": PAPER, "epub": EPUB,
    }
    # The public offer is never a proof of the EPUB format actually owned.
    assert epub.items[0].metadata["isbn_digital"] is None
    assert epub.items[0].metadata["public_detail"]["format_edition_proven"] is False

    paper = refine_loaded_public_page(page, isbn_digital=PAPER)
    assert_partial_unknown(paper, matches=0, unknown=20)
    another = refine_loaded_public_page(page, isbn_digital=PDF)
    assert_partial_unknown(another, matches=0, unknown=20)


def test_visible_pdf_or_generic_digital_label_can_match_exact_ean():
    for label in ("EAN PDF", "ISBN numérique"):
        page = with_explicit_detail(listing(), visible=f"<dt>{label}</dt><dd>{PDF}</dd>")
        result = refine_loaded_public_page(page, isbn_digital=PDF)
        assert_partial_unknown(result, matches=1, unknown=19)


def test_generic_jsonld_isbn_not_assumed_to_identify_digital_edition():
    page = with_explicit_detail(listing(), visible="", json_isbn=EPUB)
    result = refine_loaded_public_page(page, isbn_digital=EPUB)
    assert_partial_unknown(result, matches=0, unknown=20)


def test_fake_or_modified_public_evidence_is_never_a_positive_match():
    page = with_explicit_detail(
        listing(), visible="<dt>EAN EPUB</dt><dd>" + EPUB + "</dd>",
    )
    item = page.items[0]
    fake_proof = dict(item.metadata["public_detail"])
    fake_proof["identifier_evidence"] = {
        "epub": {**fake_proof["identifier_evidence"]["epub"],
                 "source_url": "https://other.invalid/?token=PRIVATE"}
    }
    manipulated = replace(item, metadata={**item.metadata, "public_detail": fake_proof})
    page = replace(page, items=(manipulated, *page.items[1:]))
    result = refine_loaded_public_page(page, isbn_digital=EPUB)
    assert_partial_unknown(result, matches=0, unknown=20)


def test_stale_product_title_proof_is_not_an_isbn_match():
    page = with_explicit_detail(
        listing(), visible="<dt>EAN EPUB</dt><dd>" + EPUB + "</dd>",
    )
    changed_title = replace(page.items[0], title="Another edition")
    stale = replace(page, items=(changed_title, *page.items[1:]))
    result = refine_loaded_public_page(stale, isbn_digital=EPUB)
    assert_partial_unknown(result, matches=0, unknown=20)


def test_local_isbn_can_combine_with_verified_author_without_global_claim():
    page = with_explicit_detail(
        listing(), visible="<dt>EAN EPUB</dt><dd>" + EPUB + "</dd>",
    )
    result = refine_loaded_public_page(
        page, isbn_digital=EPUB, author="Mourlevat",
    )
    assert result.matching_count == 1
    assert result.applied_fields == ("author", "isbn_digital")
    assert result.has_uninspected_or_unverified_matches is True
    assert result.items[0].record_id == page.items[0].record_id


@pytest.mark.parametrize("bad", [
    "", "isbn", "1234567890123", "9782092591094",
    "9782092591093\nSECRET", True, 9782092591093, None, ["9782092591093"],
])
def test_invalid_or_non_isbn13_is_rejected_before_page_scan(bad):
    with pytest.raises(ProviderCapabilityError, match="invalid_local_filter"):
        refine_loaded_public_page(listing(), isbn_digital=bad)


def test_isbn_with_separators_is_normalized_to_an_exact_13_digit_match():
    page = with_explicit_detail(
        listing(), visible="<dt>EAN EPUB</dt><dd>" + EPUB + "</dd>",
    )
    result = refine_loaded_public_page(page, isbn_digital="978-2-09259-109-3")
    assert_partial_unknown(result, matches=1, unknown=19)
