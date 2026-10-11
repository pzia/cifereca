"""P2 #700: indexed facet candidates must preserve every deterministic edge."""
from __future__ import annotations

import random

import constellations as target


def item(i, *, sparse=False):
    metadata = {
        "subjects": [f"subject-{i}" if sparse else f"subject-{i % 5}"],
        "series": "" if sparse else f"cycle-{i % 4}",
        "language": "" if sparse else ("fr" if i % 2 else "en"),
        "formats": [] if sparse else (["epub"] if i % 3 else ["pdf"]),
        "publisher": "" if sparse else f"éditeur-{i % 6}",
    }
    return {
        "book_id": str(i),
        "title": f"Livre {i}",
        "authors": [f"autrice-{i}" if sparse else f"autrice-{i % 7}"],
        "metadata": metadata,
    }


def reference_edges(books):
    normalized = target.deduplicate_books(books)
    expected = {}
    for i, left in enumerate(normalized):
        for right in normalized[i + 1:]:
            edge = target.relation(left, right)
            if edge is not None:
                expected[(left.key, right.key)] = edge
    return expected


def test_indexed_graph_is_byte_equivalent_to_naive_all_pairs():
    # Multiple overlap channels, missing fields, duplicate titles and keys.
    source = [item(i) for i in range(90)]
    source.extend([
        {"book_id": "repeat", "title": "édition", "authors": ["unique"],
         "metadata": {"series": "cycle-2", "subjects": ["subject-3"]}},
        {"book_id": "repeat", "title": "édition", "authors": ["unique"],
         "metadata": {"subjects": ["subject-4"], "publisher": "éditeur-1"}},
        {"book_id": "blank", "title": "sans facettes"},
    ])
    rng = random.Random(700)
    rng.shuffle(source)
    expected = reference_edges(source)
    graph = target.ConstellationGraph(source)
    assert graph._edges == expected
    # Reverse lookup and neighbor reasons/strength/order also remain identical.
    for (left, right), edge in expected.items():
        assert graph.edge(right, left).strength == edge.strength
        assert graph.edge(left, right).reasons == edge.reasons
    for key in graph.books:
        actual = graph.neighbors(key)
        expected_neighbors = sorted(
            ((edge if edge.left == key else target.Relation(
                key, edge.left, edge.strength, edge.reasons))
             for edge in expected.values() if key in (edge.left, edge.right)),
            key=lambda edge: (-edge.strength, edge.right),
        )
        assert actual == tuple(expected_neighbors)


def test_sparse_600_books_avoid_quadratic_relation_calls(monkeypatch):
    calls = 0
    original = target.relation

    def monitored(left, right):
        nonlocal calls
        calls += 1
        return original(left, right)

    monkeypatch.setattr(target, "relation", monitored)
    graph = target.ConstellationGraph(item(i, sparse=True) for i in range(600))
    assert len(graph.books) == 600
    assert not graph._edges
    assert calls == 0  # zero shared attested facets, no candidate pair


def test_dense_books_still_keep_all_expected_edges(monkeypatch):
    source = [
        {"book_id": str(i), "title": f"Book{i}", "authors": [f"author{i}"],
         "metadata": {"language": "fr"}}
        for i in range(24)
    ]
    calls = 0
    original = target.relation

    def monitored(left, right):
        nonlocal calls
        calls += 1
        return original(left, right)

    monkeypatch.setattr(target, "relation", monitored)
    graph = target.ConstellationGraph(source)
    assert len(graph._edges) == 24 * 23 // 2
    assert calls == 24 * 23 // 2
    assert all(
        edge.reasons == (target.RelationReason("language", "fr", .05),)
        for edge in graph._edges.values()
    )


def test_different_facet_kinds_do_not_create_false_link():
    # Same string value in different bibliographic fields is NOT a reason.
    graph = target.ConstellationGraph([
        {"book_id": "one", "title": "One", "metadata": {"subjects": ["Soleil"]}},
        {"book_id": "two", "title": "Two", "metadata": {"series": "Soleil"}},
    ])
    assert not graph._edges
