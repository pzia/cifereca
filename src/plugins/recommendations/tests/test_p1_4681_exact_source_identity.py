"""#4681/#2385: identity of exact catalogue records cannot collapse accounts.

Read-only ranking fixtures. No entitlement, acquisition or network call.
"""
from recommendation_engine import (
    build_profile, candidate_key, identifier_values, is_already_present,
)


def source(*, credential="card-A", docbase="EBOOKS", collection="collection-1"):
    return {
        "title": "Livre homonyme", "author": "Auteur",
        "source_provider": "paris", "source_credential": credential,
        "record_id": "record-1", "docbase": docbase,
        "source_collection": collection,
    }


def test_same_exact_notice_is_present_without_leaking_account_coordinates():
    book = source()
    identifiers = identifier_values(book)
    assert len(identifiers) == 1
    ident = next(iter(identifiers))
    assert ident.startswith("source-exact.v1:")
    assert "card-A" not in ident and "record-1" not in ident
    assert is_already_present(build_profile([book]), source())


def test_same_provider_record_but_different_account_or_collection_not_owned():
    own = source()
    profile = build_profile([own])
    for other in (
        source(credential="card-B"),
        source(docbase="PHYSICAL"),
        source(collection="collection-2"),
        source(collection=None),
    ):
        assert not is_already_present(profile, other)
        assert candidate_key(own) != candidate_key(other)
    assert profile.total_books == 1


def test_partial_scope_fails_closed_instead_of_fuzzy_title_match():
    own = source()
    profile = build_profile([own])
    incomplete = {
        "title": own["title"], "author": own["author"],
        "source_provider": "paris", "source_credential": "card-A",
        "record_id": "record-1",  # no docbase: cannot attest exact route
    }
    assert identifier_values(incomplete) == set()
    assert not is_already_present(profile, incomplete)
    malformed = {**own, "source_collection": object()}
    assert identifier_values(malformed) == set()
    assert not is_already_present(profile, malformed)


def test_legacy_unscoped_identifier_compatibility_stays_unchanged():
    old = {"identifiers": {"provider": "paris", "source_item_id": "record-1"}}
    assert identifier_values(old) == {"source:paris:record-1"}
    assert candidate_key(old) == "id:source:paris:record-1"
    # Legacy unscoped evidence alone cannot prove an exact scoped account.
    assert not is_already_present(build_profile([old]), source())


def test_strong_checksum_validated_isbn_remains_independently_applicable():
    one = {**source(), "isbn": "9780306406157"}
    another = {**source(credential="card-B"), "isbn": "9780306406157"}
    # Shared valid own ISBN remains a bibliographic signal, not an inherited
    # right or source-account permission; source coordinates are still distinct.
    assert identifier_values(one) & identifier_values(another) == {
        "isbn:9780306406157",
    }
    assert is_already_present(build_profile([one]), another)
