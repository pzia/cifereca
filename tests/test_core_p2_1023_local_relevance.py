"""#1023: local search uses only indexed bibliographic facts, never network."""
from __future__ import annotations

from cifereca.core.search import (
    KnownCatalogSearch, LocalLibrarySearch, filter_loaded_results,
    local_search_evidence, local_match_explanation,
)


def row(item, title, **metadata):
    return {
        "item_id": item, "provider_id": "kobo", "title": title,
        "author": "Auteur fictif", "media_format": "epub", "metadata": metadata,
    }


def test_prix_du_quai_stopword_split_fields_and_ranked_reasons():
    records = [
        row("1", "Une autre histoire", description="Prix littéraire du quai"),
        row("2", "Le Prix du Quai des Orfèvres"),
        row("3", "Le Prix oublié", series="Les enquêtes du Quai"),
        row("4", "Le Quai", awards=["Prix littéraire"]),
        row("5", "Le Prix", description="Sans référence au lieu recherché"),
    ]
    matches = filter_loaded_results(records, "prix du quai")
    assert [item["item_id"] for item in matches] == ["2", "3", "4", "1"]
    assert local_search_evidence(records[1], "prix du quai") == (200, ("title",))
    score, reasons = local_search_evidence(records[2], "prix du quai")
    assert score == 180 and reasons == ("title", "series")
    assert score > local_search_evidence(records[0], "prix du quai")[0]


def test_description_nested_catalogue_and_normalization_are_cache_only():
    rows = [
        row("1", "Sans indice", description="Le Prix du Quai est cité"),
        row("2", "Édition distincte", search_result={"description": "Prix Quai"}),
        row("3", "L’Été des Orfèvres", subjects=["quai"]),
    ]
    assert [r["item_id"] for r in filter_loaded_results(rows, "prix quai")] == ["1", "2"]
    assert [r["item_id"] for r in filter_loaded_results(rows, "l'ete quai")] == ["3"]
    assert not filter_loaded_results(rows, '"prix du quai" auteur introuvable')


def test_quoted_phrase_remains_strict_within_one_field():
    rows = [
        row("1", "Prix du Quai"),
        row("2", "Prix Quai"),
        row("3", "Prix", description="du Quai"),
    ]
    assert [r["item_id"] for r in filter_loaded_results(rows, '"Prix du Quai"')] == ["1"]
    # Typed Title remains the historical strict match: "du" is required.
    assert [r["item_id"] for r in filter_loaded_results(rows, "prix du quai", field="title")] == ["1"]
    assert [r["item_id"] for r in filter_loaded_results(rows, "Prix Quai", field="title")] == ["1", "2"]


def test_all_stopword_queries_do_not_return_all_books():
    rows = [row("1", "Voyage"), row("2", "Du voyage")]
    assert [r["item_id"] for r in filter_loaded_results(rows, "du")] == ["2"]
    assert [r["item_id"] for r in filter_loaded_results(rows, "le du")] == []
    assert filter_loaded_results(rows, "") == rows


def test_unrelated_credentials_paths_and_links_never_become_search_terms():
    confidential = "private-secret-value"
    doc = row("1", "Un roman", description="Texte anodin",
              rights=confidential, source_credential=confidential,
              local_path=f"/home/{confidential}/book.epub",
              url=f"https://example.invalid/?token={confidential}")
    assert not filter_loaded_results([doc], confidential)
    assert local_search_evidence(doc, confidential) == (0, ())
    assert [x["item_id"] for x in filter_loaded_results([doc], "roman")] == ["1"]


def test_local_and_known_catalogue_use_the_same_relevance_without_provider_calls():
    source = [
        row("description", "Histoire ordinaire", description="Prix du Quai"),
        row("title", "Prix Quai", description="Autre histoire"),
    ]
    calls = []

    def local_entries(*, identity_id, scan):
        calls.append(("local", identity_id, scan))
        # The fixture's books are Alice's only. A read under Bob must not
        # manufacture Bob-owned rows from the same private source.
        if identity_id != "alice":
            return []
        return [dict(item, identity_id="alice") for item in source]

    def catalog_entries():
        calls.append(("catalog",))
        return [dict(item, identity_id="alice", catalog_entry_id=item["item_id"],
                     local_state="remote_catalog") for item in source]

    local = LocalLibrarySearch(local_entries)
    known = KnownCatalogSearch(catalog_entries, local_entries)
    assert [item["item_id"] for item in local.search("prix du quai", identity_id="alice")] == [
        "title", "description",
    ]
    assert [item["item_id"] for item in known.search("prix du quai", identity_id="alice")] == [
        "title", "description",
    ]
    assert all(call[0] in {"local", "catalog"} for call in calls)
    assert not known.search("prix du quai", identity_id="bob")


def test_local_match_explanation_uses_attested_fields_and_respects_explicit_filter():
    record = row("x", "Le prix oublié", series="Enquête au quai",
                 description="Une autre histoire")
    assert local_search_evidence(record, "prix du quai") == (180, ("title", "series"))
    assert local_match_explanation(record, "prix du quai") == (
        "Correspondance : Titre · Série"
    )
    assert local_match_explanation(record, "prix du quai", field="title") == ""
    assert local_match_explanation(record, "introuvable") == ""
    assert local_match_explanation(record, "") == ""


def test_local_match_explanation_never_attributes_private_metadata_or_remote_proof():
    record = row("x", "Histoire", description="Le Prix du Quai",
                 source_credential="private_token", signed_url="https://private.invalid/secret")
    assert local_match_explanation(record, "prix quai") == "Correspondance : Résumé"
    assert local_match_explanation(record, "private_token") == ""
    assert local_match_explanation(record, '"prix du quai"') == "Correspondance : Résumé"
    assert local_match_explanation(record, '"prix quai"') == ""


def test_attested_plural_authors_are_searchable_even_without_display_author():
    rows = [
        {"item_id": "a", "provider_id": "kobo", "title": "Roman",
         "author": None, "metadata": {"authors": ["Alice Martin", "Bob Leroux"]}},
        {"item_id": "b", "provider_id": "kobo", "title": "Roman",
         "author": None, "metadata": {"authors": ["Anne Moreau"]}},
    ]
    assert [r["item_id"] for r in filter_loaded_results(rows, "alice", field="author")] == ["a"]
    assert [r["item_id"] for r in filter_loaded_results(rows, "alice", field="all")] == ["a"]
    assert local_match_explanation(rows[0], "alice") == "Correspondance : Auteur"
    assert local_search_evidence(rows[0], "alice") == (70, ("author",))


def test_nested_unverified_author_objects_and_credential_like_values_are_not_indexed():
    row = {
        "item_id": "a", "provider_id": "kobo", "title": "Roman",
        "metadata": {"authors": [
            {"name": "Value containing private_key"},
            "https://example.invalid/?token=private",
            "Bearer secret",
            "Valid Published Author",
        ]},
    }
    assert not filter_loaded_results([row], "private_key")
    assert not filter_loaded_results([row], "private")
    assert not filter_loaded_results([row], "secret")
    assert [r["item_id"] for r in filter_loaded_results([row], "published", field="author")] == ["a"]
