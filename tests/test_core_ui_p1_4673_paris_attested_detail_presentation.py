"""Catalogue availability is descriptive and independent of the supplier."""
from dataclasses import replace

import pytest

from cifereca.document_classification import DocumentClassification
from cifereca.gui.detail_model import catalogue_availability_label
from tests.test_p1_4276_search_document_filter import classified, notice


@pytest.mark.parametrize("provider", ["paris", "synthetic-lender"])
@pytest.mark.parametrize("raw_medium,documented,qualifies", [
    ("physical", classified("digital", "book"), True),
    ("digital", classified("physical", "book"), False),
    ("digital", DocumentClassification(), False),
    ("digital", None, True),
    ("physical", None, False),
])
def test_catalogue_borrow_label_uses_documented_support(
        provider, raw_medium, documented, qualifies):
    item = replace(
        notice("book", medium=raw_medium, doc=documented),
        source_provider=provider,
    )
    assert catalogue_availability_label(item, "Empruntable") == (
        "Empruntable selon le catalogue · compte non vérifié"
        if qualifies else "Empruntable"
    )


@pytest.mark.parametrize("availability", ["Non vérifiée", "Réservée", "Disponible"])
def test_other_catalogue_labels_keep_the_observed_status(availability):
    item = notice("book", medium="digital", doc=classified("digital", "book"))
    assert catalogue_availability_label(item, availability) == availability
