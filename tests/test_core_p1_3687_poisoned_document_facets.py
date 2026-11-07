"""#3687: malformed supplier format evidence never aborts local facet projection.

Pure Core tests: no GTK, provider API, filesystem, network or rights mutation.
"""
from __future__ import annotations

import pytest

from cifereca.core.document_filter import loaded_document_facets


FORMATS = ("epub", "pdf", "mp3")


class Opaque:
    def __str__(self):
        raise AssertionError("SUPPLIER_PRIVATE_TOKEN")

    def __repr__(self):
        raise AssertionError("SUPPLIER_PRIVATE_TOKEN")


class PoisonedMapping(dict):
    def get(self, key, default=None):
        if key == "document_classification":
            raise RuntimeError("SUPPLIER_PRIVATE_TOKEN")
        return super().get(key, default)


@pytest.mark.parametrize("row", [
    {"medium": "digital", "format": Opaque()},
    {"medium": "digital", "metadata": {"formats": [Opaque()]}},
    PoisonedMapping({"medium": "digital", "format": "EPUB"}),
    {"medium": "physical", "metadata": {"available_formats": [Opaque()]}},
])
def test_poisoned_document_reverts_to_unknown_without_leaking_payload(row):
    assert loaded_document_facets(row, FORMATS) == ("unknown", ())


def test_good_edition_survives_bad_neighbour_and_remains_filterable():
    good = {"medium": "digital", "format": "EPUB"}
    bad = {"medium": "digital", "format": Opaque()}
    rows = (good, bad, {"medium": "digital", "format": "PDF"})

    observed = [loaded_document_facets(row, FORMATS) for row in rows]

    assert observed == [
        ("digital", ("epub",)),
        ("unknown", ()),
        ("digital", ("pdf",)),
    ]
    assert observed[0] == loaded_document_facets(good, FORMATS)


def test_typed_unknown_cannot_be_replaced_by_legacy_format_guess():
    from cifereca.document_classification import DocumentClassification
    row = {
        "document_classification": DocumentClassification(),
        "medium": "digital", "format": "EPUB",
    }
    assert loaded_document_facets(row, FORMATS) == ("unknown", ())
