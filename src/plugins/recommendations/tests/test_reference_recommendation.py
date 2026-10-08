from types import SimpleNamespace

import pytest

from reference_recommendation import (
    compute_reference_recommendations,
    prepare_reference,
)


class Catalog:
    def __init__(self):
        self.queries = []

    def searchable_providers(self):
        return [("catalog", "Catalogue")]

    def search(self, query, **kwargs):
        self.queries.append((query, kwargs))
        return [
            {
                "title": "Livre référence",
                "author": "Alice Auteur; Bob Coauteur",
                "metadata": {"subjects": ["histoire spatiale"]},
                "source_provider": "catalog",
            },
            {
                "title": "Livre voisin",
                "author": "Alice Auteur",
                "metadata": {
                    "subjects": ["histoire spatiale"],
                    "languages": ["fr"],
                    "format": "epub",
                },
                "source_provider": "catalog",
            },
        ]


def reference_book():
    return SimpleNamespace(
        book_id="book-1",
        title="Livre référence",
        authors=("Alice Auteur", "Bob Coauteur"),
        identifiers={"isbn": "9781234567897"},
        metadata={
            "subjects": ["histoire spatiale"],
            "description": "Une exploration des archives orbitales.",
            "language": "fr",
            "format": "epub",
        },
    )


def test_prepare_reference_uses_multiple_authors_and_metadata_signals():
    plan = prepare_reference(reference_book(), max_queries=8)

    assert "Alice Auteur" in plan.queries
    assert "Bob Coauteur" in plan.queries
    assert any("histoire" in query or "spatiale" in query for query in plan.queries)
    assert plan.book.metadata["language"] == "fr"
    assert plan.book.metadata["format"] == "epub"


def test_reference_compute_excludes_seed_itself_and_keeps_related_candidate():
    catalog = Catalog()

    result = compute_reference_recommendations(
        catalog,
        reference_book(),
        max_queries=4,
        max_results=10,
        exploration=0,
    )

    titles = [row.item["title"] for row in result.ranked]
    assert "Livre référence" not in titles
    assert "Livre voisin" in titles
    assert catalog.queries
    assert all(call[1]["force"] is False for call in catalog.queries)


def test_reference_with_only_author_is_still_usable():
    plan = prepare_reference(
        SimpleNamespace(
            book_id="book-2",
            title="",
            authors=("Autrice Seule",),
            identifiers={},
            metadata={"language": "en", "format": "pdf"},
        )
    )

    assert plan.queries == ("Autrice Seule",)


def test_reference_without_recommendation_signal_is_rejected():
    with pytest.raises(ValueError, match="pas assez de métadonnées"):
        prepare_reference(
            SimpleNamespace(
                book_id="book-empty",
                title="",
                authors=(),
                identifiers={},
                metadata={"language": "fr", "format": "epub"},
            )
        )
