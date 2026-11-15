"""P1 #4761: public bibliography survives from Search into Recommendations detail.

Uses one dictionary, no provider, LLM, GTK startup, account or image access.
"""
from __future__ import annotations

import pytest

from cifereca.book_detail_metadata import detail_metadata_items
from recommendations import _public_recommendation_detail_fields


def book():
    return {
        "title": "Cartes du Ciel",
        "author": "Mila Fictive",
        "source_provider": "gallica",
        "record_id": "synthetic-003",
        "metadata": {
            "subjects": ["Astronomie", "Botanique"],
            "series": "Voyages astraux",
            "collection": "Histoires polaires",
            "publisher": "Éditions Septentrion",
            "awards": ["Prix des Étoiles"],
            "isbn": "9780306406157",
            "private_marker": "DO_NOT_DISPLAY_PRIVATE",
            "acquisition_url": "https://secret.example/buy?token=PRIVATE",
        },
    }


def test_same_public_book_facts_are_renderable_in_both_detail_projections():
    item = book()
    core_facts = {(label, value) for label, value, _kind
                  in detail_metadata_items(item["metadata"])}
    projected = _public_recommendation_detail_fields(item)
    expected = {
        ("Sujet", "Astronomie"), ("Sujet", "Botanique"),
        ("Série", "Voyages astraux"),
        ("Collection", "Histoires polaires"),
        ("Éditeur", "Éditions Septentrion"),
        ("Prix littéraire", "Prix des Étoiles"),
    }
    assert expected <= core_facts
    assert expected == {(f.label, f.value) for f in projected}
    assert len({f.id for f in projected}) == len(projected)
    assert {(f.label, f.kind) for f in projected} == {
        ("Sujet", "subject"), ("Série", "series"),
        ("Collection", "collection"), ("Éditeur", "copy"),
        ("Prix littéraire", "copy"),
    }
    # ISBNs live in a separate source-exact edition section. A metadata
    # declaration alone is not rights or proof of that catalogue variant.
    assert not any(f.label == "ISBN déclaré" for f in projected)


@pytest.mark.parametrize("unsafe", [
    "https://private.example/get?token=HIDDEN",
    "token=HIDDEN",
    "Bearer: HIDDEN",
    "HIDDEN@example.net",
    "/home/private/keys",
    r"\\server\share\keys",
    "custom=HIDDEN",
])
def test_private_or_uri_like_bibliography_not_promoted_to_visible_fields(unsafe):
    item = book()
    for key in ("subjects", "series", "collection", "publisher", "awards"):
        item["metadata"][key] = [unsafe, "Astronomie"]
    projected = _public_recommendation_detail_fields(item)
    assert [(f.label, f.value) for f in projected] == [
        ("Sujet", "Astronomie"), ("Série", "Astronomie"),
        ("Collection", "Astronomie"), ("Éditeur", "Astronomie"),
        ("Prix littéraire", "Astronomie"),
    ]


def test_opaque_provider_objects_are_never_stringified_for_detail():
    class Secret:
        def __str__(self):
            raise AssertionError("PRIVATE_CREDENTIAL")

        def __repr__(self):
            raise AssertionError("PRIVATE_CREDENTIAL")

    item = book()
    item["metadata"].update(
        subjects=[Secret(), {"name": "Astronomie"}],
        series=Secret(),
        awards=[Secret(), {"label": "Prix des Étoiles"}],
        publisher=Secret(),
    )
    fields = _public_recommendation_detail_fields(item)
    assert [(f.label, f.value) for f in fields] == [
        ("Sujet", "Astronomie"),
        ("Collection", "Histoires polaires"),
        ("Prix littéraire", "Prix des Étoiles"),
    ]


def test_detail_facts_are_bounded_and_deduplicated_stably():
    item = book()
    item["metadata"]["subjects"] = [
        "Astronomie", "astronomie", " ASTRONOMIE ", *[
            f"Sujet {i}" for i in range(10000)
        ],
    ]
    fields = _public_recommendation_detail_fields(item)
    subjects = [f for f in fields if f.label == "Sujet"]
    assert len(subjects) == 8
    assert subjects[0].value == "Astronomie"
    assert len(fields) <= 40
    assert [f.value for f in fields] == [f.value for f in
        _public_recommendation_detail_fields(item)]


def test_empty_and_untrusted_fields_do_not_show_empty_section():
    item = {"title": "Sans metadata", "metadata": {
        "subjects": [], "series": [], "publisher": "token=HIDDEN",
    }}
    assert _public_recommendation_detail_fields(item) == ()
