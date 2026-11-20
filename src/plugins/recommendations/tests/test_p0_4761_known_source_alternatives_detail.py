"""Passive presentation of exact Core-admitted same-notice alternatives."""
from types import SimpleNamespace

import pytest

from cifereca.plugin_api import SearchResult
from recommendations import _known_source_alternative_fields


def item():
    return SearchResult(
        id="known-c:synthetic", record_id="main-record", docbase="TEST",
        medium="digital", title="Titre principal", author="Auteur principal",
        type="book", format=None, publisher=None, published_at=None,
        description=None, thumbnail_url=None, friendly_url=None,
        source_provider="seller-a", source_credential="",
        source_collection="notice_c_public", scenario="notice_c_public",
        metadata={"notice_c_known": True, "notice_c_source_alternatives": [
            {"provider_id": "seller-b", "record_id": "opaque-record",
             "source_revision": "opaque-revision", "title": "Autre titre",
             "author": "Autre auteur", "publisher": "Éditeur B",
             "series": ["Série B"], "subjects": ["Astronomie", "Nature"],
             "description": "Résumé B", "download_url": "https://private.invalid/",
             "credentials": "PRIVATE"},
            {"provider_id": "seller-c", "title": "Titre C", "subjects": ["Cuisine"]},
        ]},
    )


def project(row, admitted=True, providers=()):
    return _known_source_alternative_fields(
        row, admitted=admitted, catalogue_sources=providers,
    )


def test_independent_descriptive_rows_keep_primary_facts_and_source_order():
    row = item()
    b, c = project(row)
    assert b.label == "Source 2 · seller-b"
    assert c.label == "Source 3 · seller-c"
    assert b.kind == c.kind == "text"
    assert "Titre : Autre titre" in b.value
    assert "Sujets : Astronomie ; Nature" in b.value
    assert "Série : Série B" in b.value
    assert "Résumé (extrait) : Résumé B" in b.value
    assert c.value == "Titre : Titre C\nSujets : Cuisine"
    assert all(value not in repr((b, c)) for value in (
        "opaque-record", "opaque-revision", "PRIVATE", "https://",
    ))
    assert row.title == "Titre principal" and row.author == "Auteur principal"
    assert project(row, providers=("seller-c",)) == (c,)


@pytest.mark.parametrize("mode", ["no-host-proof", "marker-only", "not-native", "over-budget"])
def test_alternatives_are_not_rendered_without_exact_admitted_shape(mode):
    row = item()
    if mode == "no-host-proof":
        assert project(row, admitted=False) == ()
    elif mode == "marker-only":
        row.metadata["notice_c_known"] = False
        assert project(row) == ()
    elif mode == "not-native":
        assert project(SimpleNamespace(metadata=row.metadata)) == ()
    else:
        row.metadata["notice_c_source_alternatives"] *= 16
        assert project(row) == ()


def test_untrusted_values_do_not_reach_visible_source_alternatives():
    row = item()
    row.metadata["notice_c_source_alternatives"] = [
        {"provider_id": "https://private.invalid/", "title": "Ignored"},
        {"provider_id": "seller-b", "title": "token=SECRET",
         "subjects": ["safe", "https://private.invalid/", object(), "a\u202eb"],
         "description": "x" * 9000},
    ]
    field, = project(row)
    assert field.value == "Sujets : safe"


def test_source_excerpts_and_list_budget_are_bounded():
    row = item()
    row.metadata["notice_c_source_alternatives"] = [
        {"provider_id": "seller-b", "description": "x" * 8000,
         "subjects": [f"Sujet {i}" for i in range(30)]},
    ]
    field, = project(row)
    assert "Sujet 7" in field.value and "Sujet 8" not in field.value
    assert field.value.endswith("x" * 512 + "…")
    assert "Sujet 7 ; …" in field.value
    assert not field.value.endswith("x" * 513 + "…")


def test_singular_classification_is_kept_in_passive_source_detail():
    row = item()
    row.metadata["notice_c_source_alternatives"] = [
        {"provider_id": "seller-b", "subject": "Physique", "genre": "Essai"},
    ]
    field, = project(row)
    assert field.value == "Sujet : Physique\nGenre : Essai"
