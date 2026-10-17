"""P2 #764 Paris: only fields present in upstream JSON become facets.

The public Paris catalogue exposes subjects, collection and audience; these
fixtures are SYNTHETIC and do not prove the search-API field names.
"""
from __future__ import annotations

from cifereca_paris_bibliography import project_bibliographic_facets
from cifereca_paris_normalize import normalize_search_result


def record(**values):
    return {
        "Resource": {
            "RscId": "586342", "RscBase": "SYRACUSE",
            "Ttl": "Les secrets de la cuisine", "Pbls": "Bayard jeunesse",
            "Desc": "<p>Découvrir la chimie quotidienne</p>",
            "Langue": "fr",
            **values,
        }
    }


def test_explicit_public_concepts_from_api_fields_only():
    raw = record(
        Sujet=["Cuisine -- ouvrages pour la jeunesse", "Sciences"],
        Collection="La Petite encyclopédie Youpi des grands curieux",
        Contributors=["Rémi Saillard. Illustrateur"],
        PublicVise="Jeunesse",
    )
    item = normalize_search_result(raw, "CATALOGUE")
    assert item.title == "Les secrets de la cuisine"
    assert item.metadata["subjects"] == ["Cuisine -- ouvrages pour la jeunesse", "Sciences"]
    assert item.metadata["collections"] == [
        "La Petite encyclopédie Youpi des grands curieux",
    ]
    assert item.metadata["contributors"] == ["Rémi Saillard. Illustrateur"]
    assert item.metadata["audience"] == "Jeunesse"
    assert item.metadata["audiences"] == ["Jeunesse"]
    assert item.metadata["bibliography_evidence"] == {
        "subjects": ["Resource.Sujet"], "collections": ["Resource.Collection"],
        "contributors": ["Resource.Contributors"], "audiences": ["Resource.PublicVise"],
    }
    assert "age" not in item.metadata and "age_min" not in item.metadata
    assert "isbn" not in item.metadata
    assert item.metadata["language"] == "fr"
    assert item.description == "Découvrir la chimie quotidienne"


def test_no_facets_when_search_api_does_not_expose_fields():
    item = normalize_search_result(record(), "CATALOGUE")
    for name in ("subjects", "collections", "audience", "series", "bibliography_evidence"):
        assert name not in item.metadata


def test_duplicate_and_conflicting_values_preserve_sources_and_ambiguities():
    raw = record(
        Subject=["Science", "Cuisine", "science", "Histoire"],
        Collection=["Cycle A"], Serie="Tome A",
    )
    raw["AdditionalProperties"] = {
        "Sujets": ["Cuisine", "Écologie"],
        "Collections": ["Cycle B"], "Series": ["Tome B"],
        "Audience": "Adulte",
    }
    item = normalize_search_result(raw, "CATALOGUE")
    assert item.metadata["subjects"] == ["Science", "Cuisine", "Histoire", "Écologie"]
    assert item.metadata["collections"] == ["Cycle A", "Cycle B"]
    assert item.metadata["series_names"] == ["Tome A", "Tome B"]
    assert "series" not in item.metadata  # no arbitrary winner
    assert item.metadata["audience"] == "Adulte"
    assert item.metadata["bibliography_evidence"]["subjects"] == [
        "Resource.Subject", "AdditionalProperties.Sujets",
    ]
    assert item.metadata["bibliography_evidence"]["collections"] == [
        "Resource.Collection", "AdditionalProperties.Collections",
    ]


def test_single_attested_series_retains_simple_consumer_scalar():
    item = normalize_search_result(record(Serie="Collection XYZ"), "CATALOGUE")
    assert item.metadata["series"] == "Collection XYZ"
    assert item.metadata["series_names"] == ["Collection XYZ"]
    assert item.metadata["bibliography_evidence"]["series_names"] == ["Resource.Serie"]


def test_no_implicit_juvenile_age_or_unverified_edition_isbn():
    raw = record(
        PublicVise="À partir de 9 ans",
        ISBN="978-2-35488-036-1",
        EAN="9782354880361",
        Note="Bons lecteurs (à partir de 9 ans)",
        Frmt="MONE",
    )
    item = normalize_search_result(raw, "EBOOKS")
    for key in ("audience", "audiences", "age", "age_min", "isbn", "ean"):
        assert key not in item.metadata
    assert item.metadata["paris_bibliographic_format"] == "MONE"
    assert item.format is None  # MONE is not a file format


def test_rejects_nested_objects_html_script_and_oversized_or_hostile_values():
    raw = record(
        Subject=[
            {"name": "Hacked", "token": "SECRET_ACCESS_TOKEN"},
            "<script>secret()</script><p>Science</p>",
            "Z" * 5000,
            None,
        ],
        Collection={"Value": "Do not guess"},
        Contributors=["\n\t", "B" * 300],
        PublicVise={"sensitive": "secret"},
    )
    item = normalize_search_result(raw, "CATALOGUE")
    assert item.metadata.get("subjects") == ["Science"]
    assert "SECRET_ACCESS_TOKEN" not in repr(item.metadata)
    assert "secret()" not in repr(item.metadata)
    assert "collections" not in item.metadata
    assert "audiences" not in item.metadata
    assert "contributors" not in item.metadata


def test_limits_number_of_public_facets_and_never_emits_raw_upstream_json():
    raw = record(Subjects=[f"Theme {i}" for i in range(50)])
    result = project_bibliographic_facets(raw["Resource"], raw)
    assert result["subjects"] == [f"Theme {i}" for i in range(12)]
    assert result["bibliography_evidence"] == {"subjects": ["Resource.Subjects"]}
    assert "raw" not in result and "AdditionalProperties" not in result
