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


def test_credentials_in_identifier_map_never_become_public_ranking_keys():
    private = {
        "title": "Titre neutre", "author": "Auteure",
        "identifiers": {
            "access_token": "PRIVATE_ACCESS_TOKEN",
            "refresh_token": "PRIVATE_REFRESH_TOKEN",
            "account_id": "PRIVATE_ACCOUNT_ID",
            "session_id": "PRIVATE_SESSION_ID",
            "password": "PRIVATE_PASSWORD",
            "custom_key": "PRIVATE_PROVIDER_SECRET",
        },
    }
    assert identifier_values(private) == set()
    key = candidate_key(private)
    assert key.startswith("ta:")
    assert "PRIVATE_" not in key
    assert not is_already_present(build_profile([private]), {
        "title": "Autre", "identifiers": {"access_token": "PRIVATE_ACCESS_TOKEN"},
    })


def test_public_schemes_remain_namespaced_but_private_value_shapes_are_refused():
    known = {
        "identifiers": {
            "doi": "10.1000/xyz", "ark": "ark:/12345/book",
            "oclc": "123456", "lccn": "2020123456",
        },
    }
    assert identifier_values(known) == {
        "doi:10.1000/xyz", "ark:ark:/12345/book",
        "oclc:123456", "lccn:2020123456",
    }
    for malformed in (
        "user:secret@private.example",
        "https://example.org/book?token=secret",
        "prefix\\u202esecret",
        "a" * 257,
    ):
        forged = {"identifiers": {"doi": malformed}}
        assert identifier_values(forged) == set()
