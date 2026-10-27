"""P1 #1023: Sujet must find indexed metadata also used by all-field ranking.

No network, entitlements, acquisition or guessed provider filter semantics.
"""
from __future__ import annotations

import pytest

from cifereca.core.search import (
    KnownCatalogSearch, LocalLibrarySearch, filter_loaded_results,
    local_match_explanation,
)


def _item(item_id, identity_id, **metadata):
    return {
        "item_id": item_id,
        "catalog_entry_id": f"notice-{identity_id}-{item_id}",
        "identity_id": identity_id,
        "provider_id": "kobo",
        "title": "Roman ordinaire",
        "author": "Autrice fictive",
        "local_state": "remote_catalog",
        "metadata": metadata,
    }


@pytest.mark.parametrize("property_name,word", [
    ("subjects", "Botanique"),
    ("subject", "Astronomie"),
    ("themes", "Mycologie"),
    ("keywords", "Orfèvrerie"),
    ("tags", "Cartographie"),
    ("genres", "Policier"),
    ("categories", "Sciences"),
])
def test_explicit_subject_matches_same_already_indexed_classification_as_all(
    property_name, word,
):
    entry = _item("edition-1", "alice", **{property_name: [word]})
    assert filter_loaded_results([entry], word, field="subject") == [entry]
    assert filter_loaded_results([entry], word, field="all") == [entry]
    assert local_match_explanation(entry, word) == "Correspondance : Sujet"
    assert filter_loaded_results([entry], word, field="title") == []
    assert filter_loaded_results([entry], word, field="author") == []


def test_known_search_result_subjects_are_available_only_as_bibliography():
    entry = _item(
        "edition-2", "alice",
        search_result={"subjects": ["Minéralogie"], "private_payload": {
            "password": "hidden-marker-value",
        }},
    )
    assert filter_loaded_results([entry], "Minéralogie", field="subject") == [entry]
    for field in ("subject", "all"):
        assert filter_loaded_results([entry], "hidden-marker-value", field=field) == []


def test_explicit_subject_filter_does_not_search_untrusted_nested_values():
    entry = _item(
        "edition-3", "alice",
        keywords=[{"hidden": "unverified-marker"}, "Psychologie"],
        tags={"token": "another-marker"},
        genres=[{"title": "opaque-marker"}],
    )
    assert filter_loaded_results([entry], "Psychologie", field="subject") == [entry]
    for fragment in ("unverified-marker", "another-marker", "opaque-marker"):
        assert filter_loaded_results([entry], fragment, field="subject") == []
        assert filter_loaded_results([entry], fragment, field="all") == []


def test_library_and_known_catalogue_keep_identity_scope_and_local_only():
    alice = _item("same-edition", "alice", tags=["Toponymie"])
    bob = _item("other-edition", "bob", tags=["Héraldique"])
    calls = []

    def library(*, identity_id, scan):
        assert scan is False
        calls.append(("library", identity_id))
        return [row for row in (alice, bob) if row["identity_id"] == identity_id]

    def catalogue():
        calls.append(("catalogue",))
        return [alice, bob]

    local = LocalLibrarySearch(library)
    known = KnownCatalogSearch(catalogue, library)
    for search in (local, known):
        assert [row["item_id"] for row in search.search(
            "Toponymie", identity_id="alice", field="subject",
        )] == ["same-edition"]
        assert search.search("Héraldique", identity_id="alice", field="subject") == []
        assert [row["item_id"] for row in search.search(
            "Héraldique", identity_id="bob", field="subject",
        )] == ["other-edition"]
    assert calls and all(row[0] in {"library", "catalogue"} for row in calls)
