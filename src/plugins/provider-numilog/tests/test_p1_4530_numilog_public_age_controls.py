"""#4530: observed public Numilog controls (not catalogue coverage).

No network. Compact HTML in tests is synthetic and models checked public
labels; fixture metadata is a bounded observation, not a captured response.
"""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from cifereca_numilog_public_identifiers import (
    PublicIdentifierError, _isbn13,
    extract_public_identifiers, validated_public_identifiers,
)

FIX = Path(__file__).parent / "fixtures" / "public_age_controls_20261106.json"
CANONICAL_AGE = {
    "recommended_age", "youth_age_ranges", "attested_age_ranges",
    "attested_age_status", "public_age_range_label",
}


def _records():
    sample = json.loads(FIX.read_text(encoding="utf-8"))
    assert sample["observed_date"] == "2026-11-06"
    assert "not random" in sample["selection"]
    return sample["records"]


def _product_html(row):
    path = urlsplit(row["product_url"]).path
    isbn_sections = [
        "<p>EAN papier</p><p>" + row["isbn_paper"] + "</p>",
        "<p>" + row["isbn_epub_label"] + "</p><p>" + row["isbn_epub"] + "</p>",
    ]
    if "isbn_pdf" in row:
        isbn_sections.append(
            "<p>" + row["isbn_pdf_label"] + "</p><p>" + row["isbn_pdf"] + "</p>"
        )
    # A synopsis containing a character's age must never be mistaken for a
    # product age or become the next ISBN field's value.
    description = row.get("description_character_age_text", "Roman policier.")
    return (
        '<form id="aspnetForm" name="aspnetForm" method="post" action="'
        + path + '">'
        + "<h1>" + row["title"] + "</h1>"
        + "<p>" + description + "</p>"
        + "<h2>Détails</h2>"
        + "".join(isbn_sections)
        + "<h2>Auteur(s) du livre</h2></form>"
    )


def test_observed_controls_have_valid_explicit_format_ids_without_age_proof():
    rows = _records()
    assert len(rows) == 3
    assert len({row["product_id"] for row in rows}) == len(rows)
    for row in rows:
        assert row["product_url"].startswith(
            "https://www.numilog.com/" + row["product_id"] + "/"
        )
        assert row["editorial_reading_age_status"] == (
            "not_observed_in_inspected_collection_or_summary"
        )
        assert row["reading_age_proof"] is False
        assert not (CANONICAL_AGE & row.keys())
        for key in ("isbn_paper", "isbn_epub", "isbn_pdf"):
            if key in row:
                assert _isbn13(row[key]) == row[key]
    both = next(row for row in rows if row["product_id"] == "1889226")
    assert len({both["isbn_paper"], both["isbn_epub"], both["isbn_pdf"]}) == 3
    same = next(row for row in rows if row["product_id"] == "954158")
    assert same["isbn_paper"] == same["isbn_epub"]
    # Equality of printed/digital EANs is not evidence of ownership or age.
    character = next(row for row in rows if row["product_id"] == "1873253")
    assert "dix-sept ans" in character["description_character_age_text"]


@pytest.mark.parametrize("index", [0, 1, 2])
def test_identifier_parser_preserves_per_format_eans_with_no_age_inference(index):
    row = _records()[index]
    detail = extract_public_identifiers(
        _product_html(row), row["product_url"],
        expected_title=row["title"], require_product_form=True,
    )
    observed = {"paper": row["isbn_paper"], "epub": row["isbn_epub"]}
    if "isbn_pdf" in row:
        observed["pdf"] = row["isbn_pdf"]
    assert detail["identifiers"] == observed
    validated, evidence = validated_public_identifiers({
        "public_identifiers": detail["identifiers"],
        "identifier_evidence": detail["identifier_evidence"],
    }, row["product_url"])
    assert validated == observed
    for kind, proof in evidence.items():
        assert proof["source_url"] == row["product_url"]
        assert proof["owned_format_proven"] is False
        assert proof["public_offer_format"] == (
            kind if kind in {"epub", "pdf"} else None
        )
    assert not (CANONICAL_AGE & detail.keys())
    assert "isbn_digital" not in detail


def test_evidence_cannot_be_reassigned_between_product_urls():
    rows = _records()
    detail = extract_public_identifiers(
        _product_html(rows[0]), rows[0]["product_url"],
        expected_title=rows[0]["title"], require_product_form=True,
    )
    foreign = rows[1]["product_url"]
    with pytest.raises(PublicIdentifierError, match="evidence_conflict"):
        validated_public_identifiers({
            "public_identifiers": detail["identifiers"],
            "identifier_evidence": detail["identifier_evidence"],
        }, foreign)
    assert extract_public_identifiers(
        _product_html(rows[0]), rows[0]["product_url"],
        expected_title=rows[1]["title"], require_product_form=True,
    )["identifiers"] == {}
