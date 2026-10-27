"""#3415: project only Syracuse resource fields evidenced by the capture schema.

Synthetic values exercise the observed *keys*, not the real record contents.
No account, network, stock, loan or ebook acquisition is involved.
"""
from __future__ import annotations

import pytest

from cifereca_paris_bibliography import project_observed_resource_facets
from cifereca_paris_normalize import normalize_search_result


def record(scenario, **fields):
    resource = {
        "RscId": "record-example",
        "RscBase": "DILICOM" if scenario == "EBOOKS" else "SYRACUSE",
        "Ttl": "A synthetic title",
        **fields,
    }
    return normalize_search_result({"Resource": resource}, scenario)


@pytest.mark.parametrize("scenario", ("CATALOGUE", "EBOOKS"))
def test_observed_resource_fields_projected_without_network(scenario):
    item = record(
        scenario,
        Subj=["Animals", "Science", "animals"],
        Tags="knowledge",
        BlogPostCategories=["Exhibitions"],
        BlogPostTags=["Public selection", "Reader club"],
    )
    assert item is not None
    metadata = item.metadata
    assert metadata["subjects"] == ["Animals", "Science"]
    assert metadata["tags"] == ["knowledge"]
    assert metadata["paris_blog_categories"] == ["Exhibitions"]
    assert metadata["paris_blog_tags"] == ["Public selection", "Reader club"]
    assert metadata["bibliography_evidence"]["subjects"] == {
        "source": "paris.syracuse.search",
        "source_field": "Resource.Subj",
        "scope": scenario,
        "confidence": "upstream_field_present",
        "freshness": "search_response",
    }
    assert "age_min" not in metadata and "owned" not in metadata
    assert "isbn" not in metadata


@pytest.mark.parametrize("scenario", ("CATALOGUE", "EBOOKS"))
def test_absent_and_empty_fields_do_not_claim_evidence(scenario):
    bare = record(scenario)
    empty = record(
        scenario, Subj=[], Tags=" ", BlogPostCategories=None, BlogPostTags=[]
    )
    assert bare is not None and empty is not None
    for item in (bare, empty):
        assert "subjects" not in item.metadata
        assert "tags" not in item.metadata
        assert "paris_blog_categories" not in item.metadata
        assert "bibliography_evidence" not in item.metadata


def test_partial_values_do_not_infer_age_edition_or_server_search():
    item = record(
        "EBOOKS",
        Subj=["Jeunesse"],
        Tags=["Édition papier"],
        Culture="fr-FR",
        ISBN="9781234567890",
        Frmt="MONE",
        AdditionalProperties={"Subject": "should-not-project"},
    )
    assert item is not None
    assert item.metadata["subjects"] == ["Jeunesse"]
    for key in ("age", "age_min", "isbn", "ean", "audience",
                "series", "search_facets", "filter_capabilities"):
        assert key not in item.metadata
    assert item.metadata["paris_bibliographic_format"] == "MONE"
    assert item.format is None


def test_hostile_or_unverified_shapes_are_ignored():
    item = record(
        "CATALOGUE",
        Subj=[{"Title": "nested"}, "<script>danger()</script>", "Z" * 3000,
              "Valid", "valid"],
        Tags={"Value": "Unproven object shape"},
        BlogPostCategories=13,
        BlogPostTags=["&#60;img onerror=alert(1)&#62;", False, "\u0007"],
    )
    assert item is not None
    assert item.metadata["subjects"] == ["Valid"]
    assert "tags" not in item.metadata
    assert "paris_blog_categories" not in item.metadata
    assert "paris_blog_tags" not in item.metadata
    assert "danger" not in repr(item.metadata)


def test_cardinality_and_length_are_bounded_and_deduplicated():
    resource = {
        "Subj": [f"Topic {i}" for i in range(100)],
        "Tags": ["X" * 241, "A &amp; B", "a & b"],
    }
    result = project_observed_resource_facets(resource, "CATALOGUE")
    assert result["subjects"] == [f"Topic {i}" for i in range(12)]
    assert result["tags"] == ["A & B"]
    assert result["bibliography_evidence"]["tags"]["source_field"] == "Resource.Tags"
    assert project_observed_resource_facets(resource, "UNKNOWN") == {}
