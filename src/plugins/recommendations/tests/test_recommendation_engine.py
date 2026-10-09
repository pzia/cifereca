from recommendation_engine import (
    build_profile,
    build_queries,
    candidate_key,
    identifier_values,
    is_already_present,
    rank_candidates,
)


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



def test_identifier_values_namespace_provider_and_source_item_together():
    left = {
        "title": "A",
        "identifiers": {"provider": "kobo", "source_item_id": "42"},
    }
    right = {
        "title": "B",
        "identifiers": {"provider": "paris", "source_item_id": "42"},
    }
    other = {
        "title": "C",
        "identifiers": {"provider": "kobo", "source_item_id": "43"},
    }

    assert identifier_values(left) == {"source:kobo:42"}
    assert identifier_values(right) == {"source:paris:42"}
    assert identifier_values(other) == {"source:kobo:43"}
    assert candidate_key(left) != candidate_key(right)
    assert candidate_key(left) != candidate_key(other)


def test_provider_name_alone_is_not_a_strong_identifier():
    item = {
        "title": "Livre",
        "author": "Autrice",
        "identifiers": {"provider": "kobo"},
    }
    assert identifier_values(item) == set()
    assert candidate_key(item).startswith("ta:")


def test_isbn10_and_isbn13_are_checksum_validated_and_canonicalized():
    isbn10 = {"title": "A", "isbn10": "0-306-40615-2"}
    isbn13 = {"title": "B", "identifiers": {"isbn13": "9780306406157"}}
    assert identifier_values(isbn10) == {"isbn:9780306406157"}
    assert identifier_values(isbn13) == {"isbn:9780306406157"}
    assert candidate_key(isbn10) == candidate_key(isbn13)


def test_invalid_isbn_is_not_a_strong_identifier():
    item = {
        "title": "Livre",
        "author": "Autrice",
        "identifiers": {"isbn13": "9780000000000"},
    }
    assert identifier_values(item) == set()
    assert candidate_key(item).startswith("ta:")


def test_different_identifier_schemes_cannot_collide_on_same_raw_value():
    doi = {"title": "A", "identifiers": {"doi": "10.1000/xyz"}}
    ark = {"title": "B", "identifiers": {"ark": "10.1000/xyz"}}
    assert identifier_values(doi) == {"doi:101000xyz"}
    assert identifier_values(ark) == {"ark:101000xyz"}
    assert candidate_key(doi) != candidate_key(ark)


def test_owned_detection_does_not_match_provider_or_source_item_individually():
    profile = build_profile([
        {
            "title": "Possédé",
            "author": "Alice",
            "identifiers": {"provider": "kobo", "source_item_id": "42"},
        }
    ])
    same_provider_other_item = {
        "title": "Autre",
        "author": "Alice",
        "identifiers": {"provider": "kobo", "source_item_id": "43"},
    }
    other_provider_same_item = {
        "title": "Autre 2",
        "author": "Alice",
        "identifiers": {"provider": "paris", "source_item_id": "42"},
    }
    exact = {
        "title": "Autre titre",
        "author": "Bob",
        "identifiers": {"provider": "kobo", "source_item_id": "42"},
    }

    assert not is_already_present(profile, same_provider_other_item)
    assert not is_already_present(profile, other_provider_same_item)
    assert is_already_present(profile, exact)


def test_owned_detection_matches_canonical_isbn_across_10_and_13_forms():
    profile = build_profile([
        {"title": "Possédé", "author": "Alice", "isbn10": "0306406152"}
    ])
    candidate = {
        "title": "Autre titre",
        "author": "Bob",
        "isbn13": "9780306406157",
    }
    assert is_already_present(profile, candidate)
