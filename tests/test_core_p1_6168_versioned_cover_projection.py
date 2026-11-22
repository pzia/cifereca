"""#6168 N1: one immutable cover decision per exact source scope and revision."""
from __future__ import annotations

from dataclasses import FrozenInstanceError
import pytest

from cifereca.core.book_cover_projection import (
    CoverDocumentScope, ResolvedBookCover, resolve_book_cover,
)
from cifereca.core.covers import CoverCandidate


PUBLIC = "https://covers.example.org/source.jpg"
OTHER = "https://covers.example.org/other.jpg"


def catalogue(**kwargs):
    values = dict(
        kind="catalogue", identity_id="alice",
        source_key="provider:bnf/notice:exact-42", provider_id="bnf",
    )
    values.update(kwargs)
    return CoverDocumentScope(**values)


def loan(**kwargs):
    values = dict(
        kind="loan", identity_id="alice", source_key="loan-source:exact-42",
        provider_id="paris", credential_id="card-1", account_id="",
        loan_id="loan-42",
    )
    values.update(kwargs)
    return CoverDocumentScope(**values)


def test_list_and_detail_project_the_same_exact_source_snapshot():
    scope = catalogue()
    proof = CoverCandidate(PUBLIC, "origin", "exact-source", "doc:42", 80)
    from_list = resolve_book_cover(scope, (proof,), revision=7, snapshot_complete=True)
    from_detail = resolve_book_cover(scope, iter((proof,)), revision=7, snapshot_complete=True)
    assert from_list == from_detail
    assert from_list == ResolvedBookCover(
        scope, 7, "selected", "attested", uri=PUBLIC,
        tier="origin", source="exact-source",
        evidence_id="doc:42", candidate_count=1,
    )
    assert from_list.schema == "cifereca.resolved-book-cover/1"
    with pytest.raises(FrozenInstanceError):
        from_list.uri = OTHER


@pytest.mark.parametrize("scope", [
    catalogue(identity_id="bob"),
    catalogue(source_key="provider:bnf/notice:other"),
    catalogue(credential_id="credential-two"),
    catalogue(account_id="borrower-two"),
    loan(),
    loan(credential_id="card-2"),
    loan(account_id="borrower-two"),
    loan(loan_id="loan-43"),
    CoverDocumentScope(
        "placement", "alice", "local:exact-42",
        artifact_id="artifact-1", placement_id="placement-1",
    ),
    CoverDocumentScope(
        "placement", "alice", "local:exact-42",
        artifact_id="artifact-1", placement_id="placement-2",
    ),
])
def test_cross_source_and_account_do_not_alias_one_cover_decision(scope):
    a = resolve_book_cover(catalogue(), [CoverCandidate(PUBLIC, "catalog", "bnf")],
                           revision=1, snapshot_complete=True)
    b = resolve_book_cover(scope, [CoverCandidate(PUBLIC, "catalog", "bnf")],
                           revision=1, snapshot_complete=True)
    assert a.scope != b.scope
    assert a != b


@pytest.mark.parametrize("complete,expected,reason", [
    (False, "pending", "partial_snapshot"),
    (True, "missing", "complete_no_admissible_cover"),
])
def test_empty_snapshot_cannot_be_treated_as_complete_without_evidence(
    complete, expected, reason,
):
    result = resolve_book_cover(catalogue(), [], revision=2, snapshot_complete=complete)
    assert (result.status, result.reason) == (expected, reason)
    assert result.uri == ""


def test_partial_snapshot_does_not_prematurely_select_a_lower_tier():
    catalog = CoverCandidate(PUBLIC, "catalog", "bnf", "notice:42")
    pending = resolve_book_cover(catalogue(), [catalog], revision=4, snapshot_complete=False)
    complete = resolve_book_cover(catalogue(), [catalog], revision=5, snapshot_complete=True)
    assert pending.status == "pending" and pending.uri == ""
    assert complete.status == "selected" and complete.uri == PUBLIC
    assert (pending.revision, complete.revision) == (4, 5)


def test_failure_midway_through_source_never_certifies_missing():
    def broken():
        yield CoverCandidate(PUBLIC, "catalog", "bnf")
        raise RuntimeError("incomplete snapshot")
    result = resolve_book_cover(catalogue(), broken(), revision=3, snapshot_complete=True)
    assert result.status == "pending"
    assert result.reason == "source_failed"
    assert result.uri == ""


def test_oversize_iterator_is_not_published_partially():
    source = (CoverCandidate(PUBLIC, "catalog", "bnf") for _ in range(4097))
    result = resolve_book_cover(catalogue(), source, revision=3, snapshot_complete=True)
    assert (result.status, result.reason) == ("pending", "source_overflow")


def test_ambiguous_origin_is_not_decided_by_candidate_order_or_isbn():
    items = [
        CoverCandidate(PUBLIC, "origin", "loan-a", "proof-a", 90),
        CoverCandidate(OTHER, "origin", "loan-b", "proof-b", 90),
    ]
    for order in (items, list(reversed(items))):
        result = resolve_book_cover(
            catalogue(source_key="edition:isbn-not-a-join"),
            order, revision=1, snapshot_complete=True,
            attested_book_identity="",
        )
        assert result.status == "ambiguous" and result.uri == ""
        assert result.reason == "conflicting_evidence"
        assert result.candidate_count == 2


def test_catalogue_tie_break_requires_separate_attested_same_book_scope():
    items = [
        CoverCandidate(PUBLIC, "catalog", "cat.a", "evidence-a", 80),
        CoverCandidate(OTHER, "catalog", "cat.b", "evidence-b", 80),
    ]
    kwargs = dict(revision=5, snapshot_complete=True,
                  catalog_priorities={"cat.b": 9, "cat.a": 1})
    assert resolve_book_cover(catalogue(), items, **kwargs).status == "ambiguous"
    for rows in (items, list(reversed(items))):
        result = resolve_book_cover(
            catalogue(), rows, attested_book_identity="placement:attested", **kwargs,
        )
        assert result.status == "selected"
        assert result.uri == OTHER and result.source == "cat.b"


@pytest.mark.parametrize("url", [
    "https://user:password@covers.example.org/secret.png",
    "https://127.0.0.1/private.png",
    "https://covers.example.org/path?token=secret",
    "file://foreign-server/tmp/book.png",
])
def test_invalid_image_uri_never_reaches_public_projection(url):
    result = resolve_book_cover(
        catalogue(), [CoverCandidate(url, "catalog", "untrusted")],
        revision=1, snapshot_complete=True,
    )
    assert result.status == "missing" and result.uri == ""


@pytest.mark.parametrize("wrong", [
    {"identity_id": ""}, {"source_key": ""}, {"identity_id": "a\nsecret"},
    {"identity_id": 5}, {"provider_id": ""}, {"source_key": "x" * 513},
])
def test_catalogue_scope_requires_bounded_exact_coordinates(wrong):
    with pytest.raises(ValueError):
        catalogue(**wrong)


@pytest.mark.parametrize("wrong", [
    {"credential_id": ""}, {"account_id": None}, {"loan_id": ""},
])
def test_loan_scope_requires_exact_credential_account_and_loan(wrong):
    with pytest.raises(ValueError):
        loan(**wrong)


def test_local_placement_cannot_be_inferred_from_artifact_alone():
    with pytest.raises(ValueError):
        CoverDocumentScope("placement", "alice", "local:42", artifact_id="artifact-42")


@pytest.mark.parametrize("revision", [True, -1, "1", None])
def test_revision_must_be_explicit_nonnegative_integer(revision):
    with pytest.raises(ValueError):
        resolve_book_cover(catalogue(), [], revision=revision, snapshot_complete=True)


def test_completeness_is_strict_boolean_not_truthiness():
    with pytest.raises(TypeError):
        resolve_book_cover(catalogue(), [], revision=1, snapshot_complete="complete")


@pytest.mark.parametrize("field,value", [
    ("kind", "loan"),
    ("identity_id", "borrower-A"),
    ("source_key", ""),
    ("provider_id", ""),
    ("credential_id", "borrower-credential"),
    ("account_id", "borrower-account"),
    ("loan_id", "loan-A"),
    ("artifact_id", "artifact-A"),
    ("placement_id", "placement-A"),
])
def test_n1_revalidates_mutated_anonymous_public_scope(field, value):
    scope = CoverDocumentScope(
        kind="public_catalogue", identity_id=None,
        source_key="public:exact", provider_id="bnf", credential_id="__public__",
    )
    object.__setattr__(scope, field, value)
    with pytest.raises(ValueError):
        resolve_book_cover(
            scope, [CoverCandidate(PUBLIC, "catalog", "bnf")],
            revision=1, snapshot_complete=True,
        )


def test_n1_does_not_invoke_foreign_equality_for_mutated_scope_kind():
    class Hostile:
        def __eq__(self, _other):
            raise AssertionError("foreign equality must not run")

    scope = catalogue()
    object.__setattr__(scope, "kind", Hostile())
    with pytest.raises(ValueError, match="invalid cover scope kind"):
        resolve_book_cover(scope, [], revision=1, snapshot_complete=True)


def test_n1_valid_public_scope_retains_original_resolution():
    scope = CoverDocumentScope(
        kind="public_catalogue", identity_id=None,
        source_key="public:exact", provider_id="bnf", credential_id="__public__",
    )
    result = resolve_book_cover(
        scope, [CoverCandidate(PUBLIC, "catalog", "bnf")],
        revision=1, snapshot_complete=True,
    )
    assert result.status == "selected" and result.scope == scope
