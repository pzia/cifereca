"""P1 #4761: same attested book facts in Core Search and local Recommendations.

These are offline search-projection tests, not a claim that the two GTK
screens already present the same exact book/edition/source in live captures.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from cifereca.core.search import local_search_evidence
from recommendations import (
    UxRecommendationsView,
    _local_recommendation_field_values,
    _safe_local_bibliography_value,
)


def book():
    return {
        "title": "Cartes du Ciel",
        "author": "Mila Fictive",
        "source_provider": "fixture",
        "metadata": {
            "awards": ["Prix des Étoiles"],
            "prizes": ["Grande Aurore"],
            "literary_prizes": ["Plume cosmique"],
            "description": "Une traversée dans la brume du fjord.",
            "summary": "Chronique maritime d'une traversée.",
            "synopsis": "Découvrir la constellation voisine.",
            "publisher": "Éditions Septentrion",
            "series": "Voyages astraux",
            "collection": "Histoires polaires",
        },
    }


def matches(item, query, *, field="all"):
    view = object.__new__(UxRecommendationsView)
    view._score = NS(get_value=lambda: 0)
    view._query_controls = NS(
        filter_text=lambda: query,
        selected_sources=lambda: (),
        selected_languages=lambda: (),
        filter_field=lambda: field,
        matches_document=lambda _item: True,
    )
    ranked = NS(score=88, item=item, sources=("fixture",), notices=())
    return view._matches(ranked)


@pytest.mark.parametrize("query", [
    "prix etoiles",
    "grande aurore",
    "plume cosmique",
    "traversee fjord",
    "chronique maritime",
    "constellation voisine",
    "septentrion",
    "voyages astraux",
    "histoires polaires",
    "mila brume",
    '"prix des etoiles" fictive',
])
def test_one_documentary_object_is_searchable_by_both_core_and_recommendations(query):
    item = book()
    # Same ordinary normalized public bibliographic object is handed to the
    # two independently implemented LOCAL search contracts.
    assert local_search_evidence(item, query)[0] > 0
    assert matches(item, query)


@pytest.mark.parametrize(("field", "fact"), [
    ("award", "Prix des Étoiles"),
    ("description", "Une traversée dans la brume du fjord."),
    ("publisher", "Éditions Septentrion"),
])
def test_only_public_attested_fields_enter_the_new_projection(field, fact):
    assert fact in _local_recommendation_field_values(book(), field)


@pytest.mark.parametrize("payload", [
    "https://server.example/loan?token=PRIVATE_TOKEN",
    "token=PRIVATE_TOKEN",
    "Bearer: PRIVATE_TOKEN",
    "/home/private/credentials",
    "\\\\fileserver\\secret",
    "https://example.org/books?signature=PRIVATE_TOKEN",
])
def test_signed_urls_and_secrets_never_enter_searchable_book_facts(payload):
    assert _safe_local_bibliography_value(payload) == ""
    item = book()
    item["metadata"].update(
        awards=[payload],
        prizes=[],
        literary_prizes=[],
        description=payload,
        summary=payload,
        synopsis=payload,
        publisher=payload,
    )
    assert not matches(item, "PRIVATE_TOKEN")
    assert not matches(item, "fileserver")
    assert matches(item, "Cartes")


def test_malformed_opaque_supplier_metadata_cannot_be_stringified():
    class Secret:
        def __str__(self):
            raise AssertionError("PRIVATE_SECRET")

        def __repr__(self):
            raise AssertionError("PRIVATE_SECRET")

    item = book()
    item["metadata"].update(
        awards=[Secret(), {"label": Secret()}, {"name": "Prix des Étoiles"}],
        summary=Secret(),
        description=["Brume", Secret()],
        synopsis=Secret(),
    )
    assert matches(item, "prix etoiles")
    assert not matches(item, "PRIVATE_SECRET")


def test_new_default_fields_do_not_weaken_explicit_filter_field():
    item = book()
    assert matches(item, "prix etoiles")
    assert not matches(item, "prix etoiles", field="title")
    assert not matches(item, "brume", field="subject")
    assert matches(item, "cartes", field="title")
