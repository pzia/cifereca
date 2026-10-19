"""P0 #1886 — local author and commercial format filters recheck cached rows.

No network, accounts, public purchase requests or production credentials.
"""
from dataclasses import replace
from pathlib import Path

import pytest

from cifereca_numilog_public import parse_public_search
from cifereca_numilog_public_local_filter import refine_loaded_public_page

FIX = Path(__file__).parent / "fixtures" / "public_search_jefferson_20261016.html"
SOURCE = ("https://www.numilog.com/Pages/Recherche/ResultatRecherche.aspx"
          "?mode=brSimple&query=jefferson")
_RAW_PAGE = parse_public_search(FIX.read_text(), SOURCE)
PAGE = replace(
    _RAW_PAGE,
    items=tuple(replace(row, source_provider="numilog-catalog")
                for row in _RAW_PAGE.items),
)


def singleton(index, *, field=None, value=None, display=None, fmt=None):
    row = PAGE.items[index]
    meta = dict(row.metadata)
    if field is not None:
        meta[field] = value
    changes = {"metadata": meta}
    if display is not None:
        changes["author"] = display
    if fmt is not None:
        changes["format"] = fmt
    return replace(PAGE, items=(replace(row, **changes),))


def test_valid_author_and_format_retained_after_cache_proof_recheck():
    original = singleton(0)
    row = original.items[0]
    assert refine_loaded_public_page(original, author="Mourlevat").matching_count == 1
    pdf_index = next(
        i for i, book in enumerate(PAGE.items)
        if book.format == "PDF" and book.metadata["format_labels"] == ["PDF"]
    )
    pdf = singleton(pdf_index)
    assert refine_loaded_public_page(pdf, format_label="PDF").matching_count == 1
    assert row.owned is False and row.download_url is None


@pytest.mark.parametrize("field,value", (
    ("authors", ["PRIVATE\u202eHIDDEN"]),
    ("authors", ["PRIVATE\nSECRET"]),
    ("authors", ["x" * 1001]),
    ("authors", ["  "]),
    ("authors", ["Mourlevat", 42]),
    ("authors", ["Mourlevat"] * 33),
))
def test_mutated_author_proof_never_becomes_a_valid_local_match(field, value):
    page = singleton(0, field=field, value=value)
    result = refine_loaded_public_page(page, author="PRIVATE")
    assert result.matching_count == 0
    assert result.missing_evidence_count == 1
    assert result.has_uninspected_or_unverified_matches is True
    assert result.scope == "loaded_public_page_only"


def test_author_display_and_metadata_must_agree():
    original = PAGE.items[0]
    page = singleton(0, display="PRIVATE Unlisted Contributor")
    result = refine_loaded_public_page(page, author="Mourlevat")
    assert result.matching_count == 0
    assert result.missing_evidence_count == 1
    assert original.author != page.items[0].author


@pytest.mark.parametrize("invalid", (
    ["epub\u202ePRIVATE"], ["PDF\nPRIVATE"],
    ["PDF" * 81], ["PDF"] * 11,
    ["ePub"], ["PDF", "ePub"],
))
def test_mutated_format_labels_never_override_attested_catalogue_format(invalid):
    pdf_index = next(
        i for i, book in enumerate(PAGE.items)
        if book.format == "PDF" and book.metadata["format_labels"] == ["PDF"]
    )
    page = singleton(pdf_index, field="format_labels", value=invalid)
    output = refine_loaded_public_page(page, format_label="PDF")
    # This includes the changed multi-offer array, which must not silently
    # claim the single PDF product stored on the parsed SearchResult.
    assert output.matching_count == 0
    assert output.missing_evidence_count == 1
    assert output.has_uninspected_or_unverified_matches is True


def test_unchanged_multi_format_listing_remains_searchable_as_commercial_offer():
    index = next(
        i for i, book in enumerate(PAGE.items)
        if len(book.metadata["format_labels"]) == 2 and book.format is None
    )
    page = singleton(index)
    expected = page.items[0].metadata["format_labels"][0]
    result = refine_loaded_public_page(page, format_label=expected)
    assert result.matching_count == 1
    assert result.items[0].format is None
    assert result.items[0].owned is False and result.items[0].downloadable is False
