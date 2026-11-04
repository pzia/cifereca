"""#3415: response-page Syracuse facet *choices* are not book attributes.

All names and values are synthetic, using the observed upstream JSON shape
Label/Count/IsSelected[/DisplayLabel]. Never infer a server-side filter, a
per-record classification, a reader's age, availability, or any loan right.
"""
from __future__ import annotations

import json

import httpx
import pytest

from cifereca_paris_bibliography import project_search_response_facets
from cifereca_paris_client import ParisClient


def _choice(label="Auteurs", count=3, selected=False, **extra):
    return {"Label": label, "Count": count, "IsSelected": selected, **extra}


def _facet(choices, field="SeriesTitle_exact"):
    return {"FacetField": field, "FacetLabel": "Série",
            "FacetList": choices, "IsSearchable": True,
            "IsMultiselectable": False}


def _response(scenario, facets):
    return {"success": True, "d": {
        "Results": [{"Resource": {
            "RscId": "synthetic-edition", "Ttl": "Synthetic title",
            "RscBase": "SYRACUSE" if scenario == "CATALOGUE" else "DILICOM",
            "Type": "Livre" if scenario == "CATALOGUE" else "Livre numérique",
            "Frmt": "MONO" if scenario == "CATALOGUE" else "MONE",
        }}],
        "FacetCollectionList": facets,
    }}


@pytest.mark.parametrize("scenario", ["CATALOGUE", "EBOOKS"])
def test_official_item_shape_is_scoped_to_one_response_page(scenario):
    rows = project_search_response_facets(
        _response(scenario, [_facet([
            _choice("Série A", 12, False, DisplayLabel="Série A"),
            _choice("Série B", 0, True),
        ])]), scenario, 4,
    )
    assert len(rows) == 1
    descriptor = rows[0]
    assert descriptor["scenario"] == scenario
    assert descriptor["page"] == 4
    assert descriptor["source"] == "SearchService.d.FacetCollectionList"
    assert descriptor["scope"] == "response_page_only"
    assert descriptor["field"] == "SeriesTitle_exact"
    assert descriptor["observed_entry_count"] == 2
    assert descriptor["values_schema"] == "label_count_selected_v1"
    assert descriptor["values"] == [
        {"label": "Série A", "count": 12, "is_selected": False,
         "display_label": "Série A"},
        {"label": "Série B", "count": 0, "is_selected": True},
    ]
    assert descriptor["upstream_is_searchable"] is True
    assert descriptor["filter_capability"] == "unverified"


@pytest.mark.parametrize("bad_choice", [
    {"Label": "X", "Count": True, "IsSelected": False},
    {"Label": "X", "Count": "3", "IsSelected": False},
    {"Label": "X", "Count": -1, "IsSelected": False},
    {"Label": "X", "Count": 1_000_001, "IsSelected": False},
    {"Label": "X", "Count": 1, "IsSelected": 1},
    {"Label": "X", "Count": 1, "IsSelected": "false"},
    {"Label": "<script>", "Count": 1, "IsSelected": False},
    {"Label": "https://private.invalid/token", "Count": 1, "IsSelected": False},
    {"Label": "private@example.invalid", "Count": 1, "IsSelected": False},
    {"Label": "token=PRIVATE_SECRET", "Count": 1, "IsSelected": False},
    {"Label": "X", "Count": 1, "IsSelected": False, "DisplayLabel": "<img>"},
    {"FacetValue": "X", "Count": 1, "IsSelected": False},
    ["not", "an", "item"],
])
def test_unattested_or_hostile_item_invalidates_entire_value_list(bad_choice):
    rows = project_search_response_facets(
        _response("CATALOGUE", [_facet([
            _choice("Safe", 2), bad_choice,
        ])]), "CATALOGUE", 0,
    )
    assert len(rows) == 1
    descriptor = rows[0]
    assert descriptor["values_schema"] == "unverified"
    assert "values" not in descriptor
    assert "PRIVATE_SECRET" not in repr(descriptor)
    assert "private.invalid" not in repr(descriptor)


def test_duplicate_and_oversized_choices_do_not_generate_partial_proof():
    for choices in (
        [],
        [_choice("Series", 2), _choice("series", 1)],
        [_choice(f"Genre {i}") for i in range(65)],
        [_choice("A" * 250)],
    ):
        descriptor, = project_search_response_facets(
            _response("CATALOGUE", [_facet(choices)]), "CATALOGUE", 2,
        )
        assert descriptor["values_schema"] == "unverified"
        assert "values" not in descriptor
        assert descriptor["observed_entry_count"] == len(choices)


def test_response_facets_are_never_book_metadata_or_search_filter():
    calls = []
    def respond(request):
        scenario = json.loads(request.content)["query"]["ScenarioCode"]
        calls.append(scenario)
        return httpx.Response(200, json=_response(scenario, [
            _facet([_choice(scenario + " série", 5)], field="SeriesTitle_exact"),
            _facet([_choice("Adulte", 2)], field="Audience_exact"),
        ]))

    client = ParisClient("https://example.test")
    client.client.close()
    client.client = httpx.Client(
        transport=httpx.MockTransport(respond), trust_env=False,
    )
    try:
        books = client.search("synthetic", scope="all", page=2)
        assert len(books) == 2
        assert calls == ["CATALOGUE", "EBOOKS"]
        assert "series" not in books[0].metadata
        assert "audience" not in books[0].metadata
        assert "age_min" not in books[1].metadata
        facets = client.last_search_response_facets
        assert len(facets) == 4
        assert [r["scenario"] for r in facets] == [
            "CATALOGUE", "CATALOGUE", "EBOOKS", "EBOOKS",
        ]
        assert facets[0]["values"][0]["label"] == "CATALOGUE série"
        assert facets[2]["values"][0]["label"] == "EBOOKS série"
        assert all(r["page"] == 2 for r in facets)
        assert all(r["filter_capability"] == "unverified" for r in facets)
        assert all("search_facets" not in b.metadata for b in books)
    finally:
        client.close()
