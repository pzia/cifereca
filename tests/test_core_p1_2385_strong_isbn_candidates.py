"""#2385: ISBN is strong only when its OWN manifestation role is attested.

All synthetic and read-only; never equate print/digital, title/author or
cross-profile rights. No SQLite writes, provider requests or plugins run.
"""
from dataclasses import replace

import pytest

from cifereca.core.notice_strong_isbn_candidates import (
    OwnIsbnClaim, propose_same_manifestation_isbn_links,
)


def claim(
    source, notice, raw="9780306406157", *,
    identity="alice", namespace="isbn13", role="digital",
    own=True, edition=None, evidence="own-isbn",
):
    return OwnIsbnClaim(
        identity_id=identity, source_id=source, notice_id=notice,
        edition_id=edition or notice, evidence_ref=evidence,
        namespace=namespace, raw_value=raw,
        manifestation_role=role, own_identifier=own,
    )


def propose(*claims):
    return propose_same_manifestation_isbn_links(
        identity_id="alice", claims=list(claims),
    )


def test_same_manifestation_candidate_requires_two_proven_own_isbns():
    links = propose(claim("kobo", "book-a"),
                    claim("bnf", "book-b"))
    assert len(links) == 1
    link = links[0]
    assert link.identity_id == "alice"
    assert link.isbn13 == "9780306406157"
    assert link.manifestation_role == "digital"
    assert link.left == ("bnf", "book-b", "book-b")
    assert link.right == ("kobo", "book-a", "book-a")
    assert link.relation == "same_manifestation_candidate"
    assert not hasattr(link, "rights")
    assert not hasattr(link, "entitlement")


def test_valid_isbn10_and_isbn13_aliases_canonicalize_to_same_isbn():
    one = claim("a", "book-a", "0-306-40615-2", namespace="isbn10")
    two = claim("b", "book-b", "978-0-306-40615-7")
    links = propose(one, two)
    assert len(links) == 1
    assert links[0].isbn13 == "9780306406157"


@pytest.mark.parametrize("other", [
    claim("b", "book-b", role="print"),
    claim("b", "book-b", role="unknown"),
    claim("b", "book-b", own=False),
    claim("b", "book-b", identity="bob"),
    claim("b", "book-b", "9780306406158"),  # invalid ISBN checksum
    claim("b", "book-b", "9780306406157", namespace="isbn10"),
    claim("b", "book-b", "0306406152", namespace="isbn13"),
])
def test_no_cross_format_foreign_related_or_invalid_isbn_link(other):
    assert propose(claim("a", "book-a"), other) == ()


def test_only_own_isbn_can_link_even_when_related_format_has_same_identifier():
    own_a = claim("a", "digital-a")
    related_b = claim("b", "digital-b", own=False)
    assert propose(own_a, related_b) == ()


def test_order_duplicate_claim_and_alias_does_not_change_links():
    rows = [
        claim("a", "notice-a", "0-306-40615-2",
              namespace="isbn10", evidence="isbn10"),
        claim("a", "notice-a", "9780306406157", evidence="isbn13"),
        claim("b", "notice-b"),
        claim("b", "notice-b"),
    ]
    first = propose(*rows)
    assert first == propose(*reversed(rows))
    assert len(first) == 1
    assert first[0].left_evidence_refs == ("isbn10", "isbn13")


def test_two_different_own_identifiers_on_one_notice_refuse_partial_link():
    rows = [
        claim("a", "notice-a"),
        claim("a", "notice-a", "9781234567897"),
        claim("b", "notice-b"),
    ]
    with pytest.raises(ValueError, match="conflicting_own_isbn_claim"):
        propose(*rows)


def test_same_notice_role_or_edition_conflict_refuses_partial_link():
    original = claim("a", "notice-a")
    for changed in (
        replace(original, manifestation_role="print"),
        replace(original, edition_id="edition-changed"),
    ):
        with pytest.raises(ValueError, match="conflicting_own_isbn_claim"):
            propose(original, changed, claim("b", "notice-b"))


@pytest.mark.parametrize("bad", [
    7, "none", {"source_id": "a"}, True,
])
def test_untyped_claim_never_becomes_evidence(bad):
    with pytest.raises(ValueError, match="invalid_isbn_claim"):
        propose_same_manifestation_isbn_links(
            identity_id="alice", claims=[bad],
        )


@pytest.mark.parametrize("bad", [
    "escaped code", "../escape", "contains?token", "", "x" * 161,
])
def test_malformed_own_provenance_aborts_batch(bad):
    with pytest.raises(ValueError, match="invalid_own_isbn_provenance"):
        propose(claim("a", "book-a", evidence=bad), claim("b", "book-b"))


def test_reject_unbounded_batch_and_explosive_ambiguous_link_group():
    with pytest.raises(ValueError, match="invalid_isbn_candidate_batch"):
        propose_same_manifestation_isbn_links(
            identity_id="alice",
            claims=[claim("a", "b")] * 513,
        )
    with pytest.raises(ValueError, match="too_many_isbn_relation_candidates"):
        propose_same_manifestation_isbn_links(
            identity_id="alice",
            claims=[claim("source", f"n{n}") for n in range(130)],
        )


def test_distinct_valid_isbn_or_unknown_roles_never_merge_on_title():
    # Neither this input nor the result has title/author; similarity is not
    # a strong identifier. Print/digital editions require a separate
    # explicitly attested relationship, never an ISBN guess.
    assert propose(
        claim("a", "paper", role="print"),
        claim("b", "digital", role="digital"),
        claim("c", "other-isbn", "9781234567897"),
    ) == ()
