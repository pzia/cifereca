"""#4681: preference evidence must be explicit, scoped, and non-authorizing."""
from dataclasses import replace

import pytest

from recommendation_engine import (
    ExplicitReadingSignal, build_profile, build_queries, content_score, is_already_present,
)


def _book(key, identity, author, subject, *, source="local", state="local"):
    return {
        "key": key, "identity_id": identity, "author": author,
        "title": "Un livre sur " + subject,
        "metadata": {"subjects": [subject]},
        "source": source, "state": state,
    }


def _signal(key, preference, *, identity="alice"):
    return ExplicitReadingSignal(
        identity_id=identity, library_key=key, preference=preference,
    )


def test_provenance_purchase_loan_or_transfer_does_not_create_preference():
    local = _book("artifact:one", "alice", "Auteure A", "Histoire")
    remote_loan = {
        **local, "state": "remote_loan_active", "entitlement_type": "loan",
        "source": "entitlement", "provider_id": "paris",
    }
    remote_purchase = {
        **local, "state": "remote_entitled", "entitlement_type": "owned",
        "source": "entitlement", "provider_id": "kobo",
    }
    transferred = {**local, "source": "transfer", "provider_id": "local"}
    assert (build_profile([local]) == build_profile([remote_loan])
            == build_profile([remote_purchase])
            == build_profile([transferred]))
    assert build_profile([local], identity_id="alice", explicit_signals=[]) == build_profile([local])


def test_liked_and_not_interested_change_descriptive_counters_only():
    books = [
        _book("artifact:liked", "alice", "Auteure A", "Histoire"),
        _book("artifact:neutral", "alice", "Auteure A", "Géographie"),
        _book("artifact:hidden", "alice", "Auteur B", "Voyage", source="entitlement",
              state="remote_loan_active"),
    ]
    base = build_profile(books)
    weighted = build_profile(
        books, identity_id="alice",
        explicit_signals=[_signal("artifact:liked", "liked"),
                          _signal("artifact:hidden", "not_interested")],
    )
    assert base.authors == {"auteure a": 2, "auteur b": 1}
    assert weighted.authors == {"auteure a": 3}
    assert weighted.subjects == {"histoire": 2, "geographie": 1}
    assert "voyage" not in weighted.subjects
    assert weighted.total_books == 3
    assert build_queries(books, profile=weighted)[0] == "Auteure A"


def test_negative_signal_does_not_override_ownership_or_edition_exclusion():
    book = _book("artifact:one", "alice", "Auteure A", "Histoire")
    book["isbn"] = "9780306406157"
    profile = build_profile(
        [book], identity_id="alice",
        explicit_signals=[_signal("artifact:one", "not_interested")],
    )
    assert profile.authors == {}
    assert "isbn:9780306406157" in profile.identifiers
    assert is_already_present(profile, {"isbn": "9780306406157", "title": "Autre"}) is True


def test_feedback_is_scoped_to_exact_identity_and_library_key_only():
    books = [_book("artifact:same", "bob", "Auteur Bob", "Voyage"),
             _book("artifact:other", "alice", "Auteur Alice", "Histoire")]
    baseline = build_profile(books)
    weighted = build_profile(
        books, identity_id="alice",
        explicit_signals=[_signal("artifact:same", "not_interested")],
    )
    assert weighted == baseline  # Never apply Alice's preference to Bob's entry.
    weighted = build_profile(
        books, identity_id="alice",
        explicit_signals=[_signal("artifact:missing", "liked")],
    )
    assert weighted == baseline  # No join by similar title, author, or ISBN.


@pytest.mark.parametrize("signals,identity", [
    ([_signal("artifact:one", "liked", identity="bob")], "alice"),
    ([replace(_signal("artifact:one", "liked"), provenance="provider")], "alice"),
    ([replace(_signal("artifact:one", "liked"), preference="read")], "alice"),
    ([replace(_signal("artifact:one", "liked"), library_key="")], "alice"),
    ([_signal("artifact:one", "liked"), _signal("artifact:one", "not_interested")], "alice"),
    ([{"library_key": "artifact:one", "preference": "liked"}], "alice"),
    ([_signal("artifact:one", "liked")], None),
    ({}, "alice"),
])
def test_invalid_or_ambiguous_signal_fail_closed(signals, identity):
    with pytest.raises(ValueError, match="explicit_reading_signal"):
        build_profile(
            [_book("artifact:one", "alice", "Auteure A", "Histoire")],
            identity_id=identity, explicit_signals=signals,
        )


def test_catalogue_metadata_cannot_sneak_in_a_user_preference():
    book = _book("artifact:one", "alice", "Auteure A", "Histoire")
    forged = {
        **book, "preference": "liked", "state": "remote_entitled",
        "metadata": {**book["metadata"], "reading_feedback": "liked"},
    }
    assert build_profile([forged]) == build_profile([book])


def test_controlled_candidates_only_gain_affinity_from_explicit_liking():
    books = [
        {"key": "one", "identity_id": "alice", "title": "Premier",
         "author": "Alice", "subjects": ["histoire"]},
        {"key": "two", "identity_id": "alice", "title": "Second",
         "author": "Bob", "subjects": ["histoire"]},
    ]
    alice_candidate = {"title": "Candidat A", "author": "Alice"}
    bob_candidate = {"title": "Candidat B", "author": "Bob"}
    baseline = build_profile(books)
    assert content_score(baseline, alice_candidate)[0] == content_score(
        baseline, bob_candidate,
    )[0]
    weighted = build_profile(
        books, identity_id="alice",
        explicit_signals=[_signal("one", "liked")],
    )
    assert content_score(weighted, alice_candidate)[0] > content_score(
        weighted, bob_candidate,
    )[0]
    # A supplier state or an EPUB metadata field does not affect this score.
    assert content_score(build_profile([
        {**books[0], "state": "remote_entitled"},
        {**books[1], "state": "remote_loan_active"},
    ]), alice_candidate) == content_score(baseline, alice_candidate)


def test_public_rationale_counts_real_books_not_amplified_preference_weight():
    books = [
        _book("artifact:one", "alice", "Auteure A", "Histoire"),
        _book("artifact:two", "alice", "Auteure A", "Culture"),
    ]
    profile = build_profile(
        books, identity_id="alice",
        explicit_signals=[_signal("artifact:one", "liked")],
    )
    assert profile.authors["auteure a"] == 3
    assert profile.author_book_counts["auteure a"] == 2
    score, reasons = content_score(profile, {"title": "Nouveau", "author": "Auteure A"})
    assert score > 0
    assert "auteur déjà présent (2 livres)" in reasons
    assert "3 livres" not in " ".join(reasons)


def test_one_explicit_signal_does_not_weight_two_duplicate_core_library_keys():
    first = _book("artifact:same", "alice", "Auteure A", "Histoire")
    duplicate = {**first, "state": "remote_loan_active", "source": "entitlement"}
    with pytest.raises(ValueError, match="ambiguous_explicit_reading_signal_target"):
        build_profile(
            [first, duplicate], identity_id="alice",
            explicit_signals=[_signal("artifact:same", "liked")],
        )
    with pytest.raises(ValueError, match="ambiguous_explicit_reading_signal_target"):
        build_profile(
            [duplicate, first], identity_id="alice",
            explicit_signals=[_signal("artifact:same", "not_interested")],
        )


def test_same_key_for_other_identity_is_not_an_ambiguous_feedback_target():
    first = _book("artifact:same", "alice", "Auteure A", "Histoire")
    other = _book("artifact:same", "bob", "Auteur Bob", "Voyage")
    result = build_profile(
        [other, first], identity_id="alice",
        explicit_signals=[_signal("artifact:same", "liked")],
    )
    assert result.authors == {"auteure a": 2, "auteur bob": 1}
    assert result.subjects == {"histoire": 2, "voyage": 1}


def test_unselected_duplicated_keys_keep_legacy_neutral_projection():
    first = _book("artifact:same", "alice", "Auteure A", "Histoire")
    assert build_profile([first, dict(first)], identity_id="alice",
                         explicit_signals=[]) == build_profile([first, dict(first)])


def test_controlled_corpus_optin_changes_full_rank_without_changing_sources():
    """#4681: compare deterministic ranking, reasons and variant provenance.

    Corpus uses only local exact keys, neutral vs explicit feedback. It cannot
    derive appreciation from an ownership state or catalogue credentials.
    """
    from recommendation_engine_fast import rank_candidates_prepared

    books = [
        _book("artifact:alice", "alice", "Autrice Alice", "Histoire"),
        _book("artifact:bob", "alice", "Auteur Bob", "Voyage"),
    ]
    offers = [
        {"title": "À découvrir — Alice", "author": "Autrice Alice",
         "record_id": "edition-a", "docbase": "BOOKS",
         "source_provider": "catalogue", "source_credential": "public",
         "source_collection": "current"},
        {"title": "À découvrir — Bob", "author": "Auteur Bob",
         "record_id": "edition-b", "docbase": "BOOKS",
         "source_provider": "catalogue", "source_credential": "public",
         "source_collection": "current"},
    ]

    def ranked(signals):
        profile = build_profile(
            books, identity_id="alice", explicit_signals=signals,
        )
        # Two independent explicit queries, same catalogue ordering for both
        # arms. RRF and source variant recovery are not preference-derived.
        return rank_candidates_prepared(
            profile, [offers, offers], exploration=0, limit=2,
        )

    baseline = ranked(())
    preferred = ranked((_signal("artifact:bob", "liked"),))
    disliked = ranked((_signal("artifact:alice", "not_interested"),))
    again = ranked(())  # Returning to neutral cannot reuse preference scores.

    assert len(baseline) == len(preferred) == len(disliked) == 2
    assert [row.item["record_id"] for row in baseline] == [
        "edition-a", "edition-b",
    ]
    assert preferred[0].item["record_id"] == "edition-b"
    assert disliked[0].item["record_id"] == "edition-b"
    assert baseline == again

    def attested_variants(rows):
        return {
            (notice.source_provider, notice.source_credential,
             notice.docbase, notice.source_collection, notice.record_id)
            for ranked_row in rows for notice in ranked_row.notices
        }

    coordinates = {
        ("catalogue", "public", "BOOKS", "current", "edition-a"),
        ("catalogue", "public", "BOOKS", "current", "edition-b"),
    }
    assert attested_variants(baseline) == coordinates
    assert attested_variants(preferred) == coordinates
    assert attested_variants(disliked) == coordinates
    assert all(
        not hasattr(row, "executable")
        for rows in (baseline, preferred, disliked) for row in rows
    )
