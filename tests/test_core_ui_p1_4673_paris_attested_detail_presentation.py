"""Paris catalogue status is descriptive, not a borrower-specific right."""
from dataclasses import replace

import pytest

from cifereca.document_classification import DocumentClassification
from cifereca.gui.detail_model import paris_catalogue_availability
from tests.test_p1_4276_search_document_filter import classified, notice


@pytest.mark.parametrize("raw_medium,documented,expected", [
    ("physical", classified("digital", "book"), True),
    ("digital", classified("physical", "book"), False),
    ("digital", DocumentClassification(), False),
    ("digital", None, True),  # legacy notice with no contradictory evidence
    ("physical", None, False),
])
def test_paris_catalogue_candidate_uses_attested_support_not_raw_medium(
        raw_medium, documented, expected):
    item = replace(
        notice("book", medium=raw_medium, doc=documented),
        source_provider="paris",
    )
    candidate, display = paris_catalogue_availability(
        item, "Empruntable",
    )
    assert candidate is expected
    assert display == (
        "Empruntable selon le catalogue · compte non vérifié"
        if expected else "Empruntable"
    )


@pytest.mark.parametrize("source", ["bnf-pret", "kobo", "numilog"])
def test_other_providers_never_gain_paris_borrower_warning(source):
    item = replace(
        notice("book", medium="digital",
               doc=classified("digital", "book")),
        source_provider=source,
    )
    assert paris_catalogue_availability(item, "Empruntable") == (
        False, "Empruntable",
    )


@pytest.mark.parametrize("availability", [
    "Non vérifiée", "Réservée", "Disponible",
])
def test_paris_non_borrowable_catalogue_label_is_not_changed(availability):
    item = replace(
        notice("book", medium="digital",
               doc=classified("digital", "book")),
        source_provider="paris",
    )
    assert paris_catalogue_availability(item, availability) == (
        True, availability,
    )
