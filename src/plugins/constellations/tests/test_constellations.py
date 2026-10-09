from constellations import ConstellationGraph, ConstellationService, Walk, WalkStep, deduplicate_books


def book(book_id, title, author, **metadata):
    return {
        "book_id": book_id,
        "title": title,
        "authors": [author] if author else [],
        "identifiers": metadata.pop("identifiers", {}),
        "metadata": metadata,
    }


def test_deduplicates_attested_same_isbn_and_preserves_best_availability():
    result = deduplicate_books([
        book("a", "Livre", "Auteur", identifiers={"isbn": "9780306406157", "provider": "local"}, owned=True, subjects=["sf"]),
        book("b", "Livre", "Auteur", identifiers={"isbn": "9780306406157", "provider": "catalog"}, borrowable=True, subjects=["science-fiction"]),
    ])
    assert len(result) == 1
    assert result[0].availability == "owned"
    assert set(result[0].subjects) == {"sf", "science-fiction"}


def test_invalid_isbn_is_not_used_as_deduplication_key():
    result = deduplicate_books([
        book("a", "Alpha", "Auteur A", identifiers={"isbn": "9780000000000"}),
        book("b", "Beta", "Auteur B", identifiers={"isbn": "9780000000000"}),
    ])
    assert len(result) == 2


def test_distinct_book_ids_are_not_merged_by_title_author():
    result = deduplicate_books([
        book("a", "Même livre", "Même auteur"),
        book("b", "Même livre", "Même auteur"),
    ])
    assert len(result) == 2


def test_does_not_merge_title_only_without_author_or_identifier():
    result = deduplicate_books([book("", "Homonyme", ""), book("", "Homonyme", "")])
    assert len(result) == 2


def test_graph_relations_are_explicit_and_bounded():
    graph = ConstellationGraph([
        book("a", "A", "Auteur", series="Cycle", subjects=["sf", "espace"], language="fr", page_count=200),
        book("b", "B", "Auteur", series="Cycle", subjects=["sf"], language="fr", page_count=240),
    ])
    keys = list(graph.books)
    edge = graph.edge(keys[0], keys[1])
    assert edge is not None
    assert 0 < edge.strength <= 1
    assert {"author", "series", "subject", "language", "length"} <= {reason.kind for reason in edge.reasons}


def test_partial_metadata_is_supported():
    graph = ConstellationGraph([
        book("a", "A", "Auteur"),
        book("b", "B", "Auteur"),
        book("c", "C", "Autre"),
    ])
    seed = next(key for key, value in graph.books.items() if value.title == "A")
    assert [item.book.title for item in graph.rank(seed)] == ["B"]


def test_cache_is_per_identity_and_freshness_token():
    service = ConstellationService()
    first = service.graph_for("alice", [book("a", "A", "Auteur")], freshness_token="1")
    second = service.graph_for("alice", [book("b", "B", "Autre")], freshness_token="1")
    bob = service.graph_for("bob", [book("b", "B", "Autre")], freshness_token="1")
    refreshed = service.graph_for("alice", [book("b", "B", "Autre")], freshness_token="2")
    assert first is second
    assert bob is not first
    assert refreshed is not first
    service.invalidate("alice")
    assert service.graph_for("alice", [book("c", "C", "Autre")], freshness_token="2") is not refreshed


def test_explorer_can_prefer_more_surprising_neighbor_than_comfort():
    graph = ConstellationGraph([
        book("seed", "Seed", "Auteur", series="Cycle", subjects=["sf", "espace"], language="fr"),
        book("near", "Near", "Auteur", series="Cycle", subjects=["sf", "espace"], language="fr"),
        book("far", "Far", "Autre", subjects=["sf"]),
    ])
    seed = next(key for key, value in graph.books.items() if value.title == "Seed")
    assert graph.rank(seed, mode="comfort", exploration=0)[0].book.title == "Near"
    assert graph.rank(seed, mode="explorer", exploration=100)[0].book.title == "Far"


def test_project_mode_prefers_shared_subject_and_forward_progression():
    graph = ConstellationGraph([
        book("seed", "Intro", "A", subjects=["histoire"], difficulty="beginner"),
        book("next", "Suite", "B", subjects=["histoire"], difficulty="intermediate"),
        book("easy", "Encore intro", "C", subjects=["histoire"], difficulty="beginner"),
        book("other", "Autre", "D", subjects=["science"], difficulty="intermediate"),
    ])
    seed = next(key for key, value in graph.books.items() if value.title == "Intro")
    ranked = graph.rank(seed, mode="project")
    assert {item.book.title for item in ranked} == {"Suite", "Encore intro"}
    assert ranked[0].book.title == "Suite"
    assert "progression:+30" in ranked[0].reasons


def test_mood_mode_marks_inferred_attributes_and_ignores_unqualified():
    graph = ConstellationGraph([
        book("seed", "Seed", "A", subjects=["fiction"]),
        book("inferred", "Inferred", "B", subjects=["fiction"], mood=["sombre"], mood_inferred=True),
        book("unqualified", "Unqualified", "C", subjects=["fiction"], mood=["sombre"], mood_qualified=False),
    ])
    seed = next(key for key, value in graph.books.items() if value.title == "Seed")
    ranked = graph.rank(seed, mode="mood", mood="sombre")
    assert [item.book.title for item in ranked] == ["Inferred"]
    assert ranked[0].inferred == ("mood:sombre",)


def test_availability_state_is_exposed_in_ranked_results():
    graph = ConstellationGraph([
        book("seed", "Seed", "A", subjects=["sf"]),
        book("owned", "Owned", "B", subjects=["sf"], owned=True),
        book("borrow", "Borrow", "C", subjects=["sf"], borrowable=True),
        book("free", "Free", "D", subjects=["sf"], free=True),
    ])
    seed = next(key for key, value in graph.books.items() if value.title == "Seed")
    assert {item.book.title: item.availability for item in graph.rank(seed)} == {
        "Borrow": "borrowable", "Free": "free", "Owned": "owned",
    }


def promenade_graph():
    return ConstellationGraph([
        book("a", "A", "Author A", subjects=["history", "sea"], series="S1"),
        book("b", "B", "Author B", subjects=["sea", "travel"]),
        book("c", "C", "Author C", subjects=["travel", "art"]),
        book("d", "D", "Author D", subjects=["art", "music"]),
        book("e", "E", "Author E", subjects=["music", "future"]),
        book("x", "X", "Author X", subjects=["sea", "art"]),
        book("y", "Y", "Author Y", subjects=["art", "future"]),
    ])


def test_promenade_produces_explained_4_to_8_step_walk():
    graph = promenade_graph()
    start = next(key for key, value in graph.books.items() if value.title == "A")
    end = next(key for key, value in graph.books.items() if value.title == "E")
    walk = graph.promenade(start, end, variant="balanced")
    assert walk is not None
    assert 4 <= len(walk.steps) <= 8
    assert walk.steps[0].from_previous == ()
    assert all(step.from_previous for step in walk.steps[1:])


def test_promenade_variants_are_supported():
    graph = promenade_graph()
    start = next(key for key, value in graph.books.items() if value.title == "A")
    end = next(key for key, value in graph.books.items() if value.title == "E")
    assert graph.promenade(start, end, variant="short") is not None
    assert graph.promenade(start, end, variant="balanced") is not None
    assert graph.promenade(start, end, variant="surprising") is not None


def test_replace_step_changes_only_one_intermediate_step():
    graph = promenade_graph()
    by_title = {value.title: value for value in graph.books.values()}
    walk = Walk("balanced", tuple(WalkStep(by_title[t]) for t in ["A", "B", "C", "D", "E"]), 0)
    revised = graph.replace_step(walk, 2, by_title["X"].key)
    assert [step.book.title for step in revised.steps] == ["A", "B", "X", "D", "E"]



def test_opaque_row_never_merges_into_strongly_identified_edition_by_title_author():
    result = deduplicate_books([
        book(
            "strong",
            "Même livre",
            "Même auteur",
            identifiers={"isbn": "9780306406157"},
            subjects=["édition-identifiée"],
        ),
        {
            "title": "Même livre",
            "authors": ["Même auteur"],
            "metadata": {"subjects": ["notice-sans-identifiant"]},
        },
    ])
    assert len(result) == 2
    assert {row.key.startswith("isbn:") for row in result} == {False, True}


def test_equally_opaque_rows_can_still_collapse_by_title_author():
    first = {"title": "Même livre", "authors": ["Même auteur"], "metadata": {"subjects": ["a"]}}
    second = {"title": "Même livre", "authors": ["Même auteur"], "metadata": {"subjects": ["b"]}}
    result = deduplicate_books([first, second])
    assert len(result) == 1
    assert set(result[0].subjects) == {"a", "b"}


def test_isbn10_and_isbn13_identifier_aliases_share_canonical_key():
    result = deduplicate_books([
        book("a", "Livre", "Auteur", identifiers={"isbn10": "0306406152"}),
        book("b", "Livre", "Auteur", identifiers={"isbn13": "9780306406157"}),
    ])
    assert len(result) == 1
    assert result[0].key == "isbn:9780306406157"
    assert result[0].identifiers["isbn"] == "9780306406157"


def test_invalid_isbn_alias_is_not_promoted_to_strong_identity():
    result = deduplicate_books([
        book("a", "Alpha", "Auteur A", identifiers={"isbn13": "9780000000000"}),
        book("b", "Beta", "Auteur B", identifiers={"isbn10": "0306406153"}),
    ])
    assert len(result) == 2
    assert all(not row.key.startswith("isbn:") for row in result)


def test_cache_without_freshness_token_never_reuses_stale_graph():
    service = ConstellationService()
    first = service.graph_for("alice", [book("a", "A", "Auteur")])
    second = service.graph_for("alice", [book("b", "B", "Autre")])
    assert second is not first
    assert {row.title for row in second.books.values()} == {"B"}


def test_anonymous_identity_is_never_cached_even_with_token():
    service = ConstellationService()
    first = service.graph_for("", [book("a", "A", "Auteur")], freshness_token="1")
    second = service.graph_for("", [book("b", "B", "Autre")], freshness_token="1")
    assert second is not first
    assert {row.title for row in second.books.values()} == {"B"}
