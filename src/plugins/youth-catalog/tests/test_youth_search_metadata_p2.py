"""P2 #1023 — richer metadata discovery on Jeunesse's offline cache only."""
from __future__ import annotations

from youth_catalog import YouthCatalogIndex, normalize_record


def record(title, **extra):
    return {"title": title, "authors": ["Anne Témoin"], **extra}


def test_search_prix_du_quai_cross_fields_and_stopwords():
    index = YouthCatalogIndex()
    index.ingest("alice", "milan", [
        record("Prix du Quai des Orfèvres", record_id="one"),
        record("Le prix mystérieux", subjects=["Littérature", "Quai des Orfèvres"], record_id="two"),
        record("La piste oubliée", description="<p>Un roman salué par le Prix du Quai.</p>",
               record_id="three"),
        record("Le quai désert", description="Aucune distinction littéraire",
               record_id="four"),
        record("Autre prix", record_id="five"),
    ])
    titles = [book.title for book in index.search("alice", "prix du quai")]
    assert titles[:2] == ["Prix du Quai des Orfèvres", "Le prix mystérieux"]
    assert "La piste oubliée" in titles
    assert "Le quai désert" not in titles
    assert "Autre prix" not in titles
    # Other identities never appear, even if their metadata is a closer match.
    index.ingest("bob", "kobo", [record("Prix du Quai de Bob")])
    assert all(book.source_id != "kobo" for book in index.search("alice", "prix quai"))


def test_search_accent_punctuation_apostrophe_and_strict_quotes():
    index = YouthCatalogIndex()
    index.ingest("alice", "source", [
        record("L'École-des Orfèvres", record_id="1"),
        record("L'École des enfants", subjects=["Orfèvres"], record_id="2"),
    ])
    assert len(index.search("alice", "ecole orfevres")) == 2
    assert len(index.search("alice", "l’école, des orfèvres")) == 2
    assert [x.record_id for x in index.search("alice", '"Ecole des Orfèvres"')] == ["1"]
    assert index.search("alice", '"phrase introuvable"') == ()


def test_metadata_search_reads_real_source_fields_and_normalizes_html():
    raw = record(
        "Un titre neutre", record_id="b",
        metadata={
            "summary": "<p>Lauréat du <strong>Prix</strong> Théophile.</p><script>token()</script>",
            "series": "Chroniques du Nord",
            "awards": ["Prix de la Mer"],
            "collections": ["Série Voyage"],
        },
        publisher="Maison Édition",
    )
    book = normalize_record(raw, source_id="milan")
    assert "token" not in book.description
    assert "Lauréat" in book.description
    assert book.series == ("Chroniques du Nord",)
    assert book.awards == ("Prix de la Mer",)
    assert book.collections == ("Série Voyage",)

    index = YouthCatalogIndex()
    index.ingest("alice", "milan", [raw])
    for q in ("theophile", "nord", "voyage", "prix mer", "maison edition"):
        assert [row.record_id for row in index.search("alice", q)] == ["b"], q


def test_merge_only_on_strong_edition_evidence_preserves_description():
    index = YouthCatalogIndex()
    index.ingest("alice", "milan", [
        record("Titre source", record_id="m", isbn13="9780306406157", age_label="8+"),
    ])
    index.ingest("alice", "kobo", [
        record("Autre titre", record_id="k", isbn13="9780306406157",
               summary="Une enquête primée au bord de la mer."),
    ])
    rows = index.search("alice", "enquete primee mer")
    assert len(rows) == 1
    assert rows[0].title == "Titre source"
    assert "enquête primée" in rows[0].description
    assert {r.source_id for r in rows[0].age_ranges} == {"milan"}

    index.ingest("alice", "third", [
        record("Même titre", record_id="t", authors=["Un autre auteur"],
               description="Privé", age_label="12+"),
    ])
    assert len(index.entries("alice")) == 2
    assert len(index.search("alice", "prive")) == 1


def test_filters_preserved_strict_no_network_and_rank_title_before_summary():
    index = YouthCatalogIndex()
    index.ingest("alice", "paris", [
        record("Prix du Quai", record_id="title", age_label="10-12",
               language="fr", format=["epub"], themes=["roman"]),
        record("Une histoire", record_id="summary", age_label="10-12",
               description="Prix du Quai", language="fr", format=["pdf"]),
        record("Un autre livre", record_id="older", age_label="5-6",
               description="Prix du Quai"),
    ])
    assert [x.record_id for x in index.search("alice", "prix quai")] == [
        "title", "summary", "older",
    ]
    assert [x.record_id for x in index.search(
        "alice", "prix quai", age=11, language="fr",
        format="epub", source_id="paris"
    )] == ["title"]
    assert index.search("alice", "prix quai", source_id="milan") == ()


def test_empty_and_stopword_only_queries_do_not_overmatch():
    index = YouthCatalogIndex()
    index.ingest("alice", "source", [
        record("Le Pont", record_id="1"), record("La Mer", record_id="2"),
    ])
    assert len(index.search("alice", "")) == 2
    assert index.search("alice", "du") == ()
    assert index.search("alice", "x" * 260) == ()


def test_search_on_3000_cached_books_keeps_original_without_network():
    index = YouthCatalogIndex()
    index.ingest("alice", "source", [
        record(f"Roman volume {number}", record_id=str(number),
               metadata={"description": f"Le sujet {number} concerne la météorologie."})
        for number in range(3000)
    ])
    rows = index.search("alice", "meteorologie")
    assert len(rows) == 3000
    assert rows[0].record_id == "0"
