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
    assert "9780306406157" in profile.identifiers
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
