from recommendation_engine import build_queries, rank_candidates


def test_rank_excludes_existing_and_prefers_matching_author_subject():
    books = [
        {"title": "La ville invisible", "author": "Alice Martin", "metadata": {"subjects": ["urbanisme", "mémoire"]}, "isbn": "1111111111"},
        {"title": "Mémoire des rues", "author": "Alice Martin", "metadata": {"subjects": ["ville", "histoire"]}},
        {"title": "Océan noir", "author": "Bob Durand", "metadata": {"subjects": ["mer", "aventure"]}},
    ]
    batches = [[
        {"title": "Autres villes", "author": "Alice Martin", "metadata": {"subjects": ["urbanisme", "histoire"]}, "source_provider": "p1"},
        {"title": "La ville invisible", "author": "Alice Martin", "isbn": "1111111111", "source_provider": "p1"},
        {"title": "Cuisine rapide", "author": "C. Chef", "metadata": {"subjects": ["cuisine"]}, "source_provider": "p1"},
    ]]
    ranked = rank_candidates(books, batches)
    assert ranked[0].item["title"] == "Autres villes"
    assert all(row.item["title"] != "La ville invisible" for row in ranked)
    assert ranked[0].score >= 15


def test_rrf_rewards_candidate_found_by_multiple_profile_signals():
    books = [
        {"title": "Un", "author": "Alice Martin", "metadata": {"subjects": ["histoire"]}},
        {"title": "Deux", "author": "Alice Martin", "metadata": {"subjects": ["mémoire"]}},
    ]
    common = {"title": "Candidat", "author": "Alice Martin", "metadata": {"subjects": ["histoire"]}, "source_provider": "p1"}
    other = {"title": "Autre", "author": "Alice Martin", "source_provider": "p1"}
    ranked = rank_candidates(books, [[common, other], [common]])
    assert ranked[0].item["title"] == "Candidat"
    assert ranked[0].query_hits == 2


def test_dedup_keeps_multiple_sources():
    books = [{"title": "Seed", "author": "A. Auteur", "metadata": {"subjects": ["science"]}}]
    one = {"title": "Candidate", "author": "A. Auteur", "isbn": "9781234567890", "source_provider": "p1"}
    two = {"title": "Candidate", "author": "A. Auteur", "isbn": "9781234567890", "source_provider": "p2"}
    ranked = rank_candidates(books, [[one], [two]])
    assert len(ranked) == 1
    assert set(ranked[0].sources) == {"p1", "p2"}


def test_queries_start_with_repeated_author():
    books = [
        {"title": "Un", "author": "Alice Martin", "metadata": {"subjects": ["histoire"]}},
        {"title": "Deux", "author": "Alice Martin", "metadata": {"subjects": ["mémoire"]}},
        {"title": "Trois", "author": "Bob Durand", "metadata": {"subjects": ["voyage"]}},
    ]
    queries = build_queries(books)
    assert queries[0] == "Alice Martin"
    assert "Bob Durand" in queries


def test_mmr_increases_author_diversity():
    books = [
        {"title": "A", "author": "Alice", "metadata": {"subjects": ["science fiction"]}},
        {"title": "B", "author": "Bob", "metadata": {"subjects": ["science fiction"]}},
    ]
    batch = [
        {"title": "A1", "author": "Alice", "metadata": {"subjects": ["science fiction"]}, "source_provider": "p"},
        {"title": "A2", "author": "Alice", "metadata": {"subjects": ["science fiction"]}, "source_provider": "p"},
        {"title": "B1", "author": "Bob", "metadata": {"subjects": ["science fiction"]}, "source_provider": "p"},
    ]
    ranked = rank_candidates(books, [batch], exploration=100, limit=3)
    assert ranked[0].item["author"] == "Alice"
    assert ranked[1].item["author"] == "Bob"


def test_owned_filter_uses_explicit_identifier_before_title_author_fallback():
    books = [
        {"title": "Même titre", "author": "Alice", "isbn": "9781111111111"},
    ]
    other_edition = {
        "title": "Même titre",
        "author": "Alice",
        "isbn": "9782222222222",
        "metadata": {"subjects": ["histoire"]},
        "source_provider": "p1",
    }
    ranked = rank_candidates(books, [[other_edition]], exclude_owned=True)
    assert [row.item["isbn"] for row in ranked] == ["9782222222222"]


def test_owned_filter_can_be_disabled_explicitly():
    books = [
        {"title": "Possédé", "author": "Alice", "isbn": "9781111111111", "metadata": {"subjects": ["histoire"]}},
        {"title": "Autre", "author": "Alice", "metadata": {"subjects": ["histoire"]}},
    ]
    owned = {
        "title": "Possédé",
        "author": "Alice",
        "isbn": "9781111111111",
        "metadata": {"subjects": ["histoire"]},
        "source_provider": "p1",
    }
    assert rank_candidates(books, [[owned]], exclude_owned=True) == []
    shown = rank_candidates(books, [[owned]], exclude_owned=False)
    assert len(shown) == 1
    assert shown[0].item["title"] == "Possédé"
