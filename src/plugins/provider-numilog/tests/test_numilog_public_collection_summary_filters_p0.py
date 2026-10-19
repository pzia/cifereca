"""#1886 P0: attested public collection and excerpt text are local-only filters.

Actual offline anonymous Numilog HTML fixture, no live network, login,
private inventory, ACSM, purchasing or inferred global search totals.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from cifereca.plugin_api import ProviderCapabilityError
from cifereca_numilog_public import parse_public_search
from cifereca_numilog_public_local_filter import refine_loaded_public_page

SOURCE = "https://www.numilog.com/Pages/Recherche/ResultatRecherche.aspx?mode=brSimple&query=jefferson"
HTML = (Path(__file__).parent / "fixtures" / "public_search_jefferson_20261016.html").read_text()


def observed():
    source_page = parse_public_search(HTML, SOURCE)
    # Emulate the public provider's verified projection, not an owned account.
    return replace(
        source_page,
        items=tuple(replace(row, source_provider="numilog-catalog")
                    for row in source_page.items),
    )


def test_summary_terms_find_source_attested_excerpt_only():
    p = observed()
    matches = refine_loaded_public_page(p, summary="FRISSONNE")
    assert matches.scope == "loaded_public_page_only"
    assert matches.applied_fields == ("summary",)
    assert matches.loaded_count == 20
    assert matches.matching_count == 1
    assert matches.items[0].title == "Jefferson prend des risques"
    assert "frissonne" in matches.items[0].description
    assert matches.public_unfiltered_total == 242
    assert matches.public_page_complete is False
    assert matches.has_uninspected_or_unverified_matches is True
    assert matches.items[0].downloadable is False
    assert matches.items[0].owned is False
    assert matches.items[0].download_url is None


def test_collection_is_source_attested_and_combinable_with_author():
    p = observed()
    roman = refine_loaded_public_page(p, collection="romans junior")
    assert roman.matching_count == 2
    # Seven items publish NO collection. Their absence is unknown, not an
    # attested mismatch: never claim that only two items could match.
    assert roman.missing_evidence_count == 7
    assert roman.has_uninspected_or_unverified_matches is True
    assert all(r.metadata["collection"] == "Romans Junior" for r in roman.items)
    combined = refine_loaded_public_page(
        p, collection="ROMANS JUNIOR", author="Jean Claude Mourlevat",
        summary="FRISSONNE",
    )
    assert combined.matching_count == 1
    assert combined.items[0].title == "Jefferson prend des risques"
    assert combined.applied_fields == ("author", "collection", "summary")
    assert combined.public_unfiltered_total == roman.public_unfiltered_total == 242
    assert combined.has_uninspected_or_unverified_matches is True


@pytest.mark.parametrize("field,term,evidence_key", [
    ("collection", "Romans Junior", "collection"),
    ("summary", "frissonne", "summary"),
])
def test_missing_or_forged_provenance_is_unknown_not_a_false_negative(field, term, evidence_key):
    p = observed()
    original = p.items[0]
    metadata = dict(original.metadata)
    evidence = dict(metadata["field_evidence"])
    evidence.pop(evidence_key)
    metadata["field_evidence"] = evidence
    unproved = replace(original, metadata=metadata)
    one = replace(
        p, items=(unproved,), page_index=0, total_results=1,
        complete=True, next_url=None, pagination_links=(),
    )
    result = refine_loaded_public_page(one, **{field: term})
    assert result.items == ()
    assert result.missing_evidence_count == 1
    assert result.has_uninspected_or_unverified_matches is True

    metadata2 = dict(original.metadata)
    evidence2 = dict(metadata2["field_evidence"])
    evidence2[evidence_key] = {
        "source_url": SOURCE, "source": "footer",
        "kind": "observed_public_html",
    }
    metadata2["field_evidence"] = evidence2
    invalid = replace(one, items=(replace(original, metadata=metadata2),))
    rejected = refine_loaded_public_page(invalid, **{field: term})
    assert rejected.items == ()
    assert rejected.missing_evidence_count == 1


@pytest.mark.parametrize("field,replacement", [
    ("summary", "x" * 16001),
    ("summary", "VALID\nSECRET"),
    ("summary", ["not", "a", "string"]),
    ("collection", "x" * 2001),
    ("collection", "VALID\u200bSECRET"),
    ("collection", {"name": "private"}),
])
def test_mutated_values_are_not_proven_matches(field, replacement):
    p = observed()
    row = p.items[0]
    if field == "summary":
        row = replace(row, description=replacement)
    else:
        row = replace(row, metadata={**row.metadata, "collection": replacement})
    one = replace(p, items=(row,), total_results=1, complete=True,
                  next_url=None, pagination_links=())
    result = refine_loaded_public_page(
        one, **{field: "VALID" if isinstance(replacement, str) else "private"},
    )
    assert result.matching_count == 0
    assert result.missing_evidence_count == 1
    assert result.has_uninspected_or_unverified_matches is True


@pytest.mark.parametrize("field", ["collection", "summary"])
@pytest.mark.parametrize("empty_token", ["...", "£€", " / -- ", "\u0301"])
def test_punctuation_only_filters_remain_rejected(field, empty_token):
    with pytest.raises(ProviderCapabilityError, match="invalid_local_filter"):
        refine_loaded_public_page(observed(), **{field: empty_token})


def test_local_filter_does_not_mutate_cached_public_rows():
    p = observed()
    result = refine_loaded_public_page(p, collection="Romans Junior")
    result.items[0].metadata["collection"] = "TAMPERED"
    again = refine_loaded_public_page(p, collection="Romans Junior")
    assert again.matching_count == 2
    assert all(r.metadata["collection"] == "Romans Junior" for r in again.items)
