"""P0 #6366/#4979: exact source revisions survive public admission and dedup.

A supplier may return two *editions* with the same record ID, docbase, title,
ISBN and thumbnail URL. Neither public declarations nor federated result
deduplication may throw one away before its independent Notice C observation.
"""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace as NS

from cifereca.core.public_search_projection import (
    project_public_search_result, project_public_search_results,
)
from cifereca.core.search_orchestration import (
    _public_rows_from_response, _search_result_scope_key,
)
from cifereca.plugin_api import ProviderSearchResponse, PublicSearchNotice
from tests.test_core_p0_6366_real_search_notice_c_ingestion import public_row


def test_two_public_revisions_of_one_record_have_two_exact_declarations():
    a = public_row(revision_id="revision-A")
    b = replace(a, revision_id="revision-B")
    response = ProviderSearchResponse(
        results=[a, b],
        public_notices=(PublicSearchNotice(a), PublicSearchNotice(b)),
    )
    session = NS(
        provider_id="bnf", credential_id="__public__",
        credentials={}, _accounts=[],
    )
    projections = project_public_search_results(response)
    assert projections is not None and len(projections) == 2
    assert projections[id(a)].revision_id is None
    assert projections[id(b)].revision_id is None
    assert project_public_search_result(
        response, a, provider_id="bnf", credential_id="__public__",
    ) == projections[id(a)]
    assert project_public_search_result(
        response, b, provider_id="bnf", credential_id="__public__",
    ) == projections[id(b)]

    rows = _public_rows_from_response(response, session)
    assert len(rows) == 2
    assert [r.revision_id for r in rows] == ["revision-A", "revision-B"]
    assert all(r.thumbnail_url == a.thumbnail_url for r in rows)
    assert _search_result_scope_key(rows[0]) != _search_result_scope_key(rows[1])
    assert all(r.owned is False and r.downloadable is False for r in rows)


def test_declaration_matching_never_copies_an_account_or_revision_to_foreign_viewer():
    a = public_row(revision_id="revision-A")
    b = replace(a, revision_id="revision-B")
    mixed = ProviderSearchResponse(
        results=[b],
        public_notices=(PublicSearchNotice(a),),
    )
    assert project_public_search_results(mixed) == {}
    assert project_public_search_result(
        mixed, b, provider_id="bnf", credential_id="__public__",
    ) is None

    claimed = replace(
        a, source_account_id="private-account-A",
        owned=True, downloadable=True,
        download_url="https://delivery.example.org/private",
    )
    foreign = ProviderSearchResponse(
        results=[claimed],
        public_notices=(PublicSearchNotice(a),),
    )
    # A private SOURCE may contribute a separately declared, read-only
    # PUBLIC bibliography. Its account/rights/URL MUST NOT be carried over.
    projected = project_public_search_results(foreign)
    assert projected is not None and len(projected) == 1
    public = projected[id(claimed)]
    assert public.source_account_id is None
    assert public.owned is False and public.downloadable is False
    assert public.download_url is None and public.revision_id is None
    assert project_public_search_result(
        foreign, claimed, provider_id="bnf", credential_id="__public__",
    ) == public

    same_private = ProviderSearchResponse(
        results=[claimed],
        public_notices=(PublicSearchNotice(claimed),),
    )
    # A private *claim* is not a sanitized public declaration.
    assert project_public_search_results(same_private) == {}


def test_revision_and_manifestation_and_account_are_not_dedup_aliases():
    a = public_row(revision_id="revision-A")
    assert _search_result_scope_key(a) == _search_result_scope_key(replace(a))
    assert _search_result_scope_key(a) != _search_result_scope_key(
        replace(a, revision_id="revision-B")
    )
    assert _search_result_scope_key(a) != _search_result_scope_key(
        replace(a, source_account_id="other-account")
    )
    assert _search_result_scope_key(a) != _search_result_scope_key(
        replace(a, medium="physical")
    )
    assert _search_result_scope_key(a) != _search_result_scope_key(
        replace(a, revision_id=None)
    )


def test_invalid_version_marker_does_not_become_shared_public_provenance():
    a = public_row(revision_id="revision-A")
    for unsafe in ("x" * 130, "token=secret", "https://secret.invalid", "in valid"):
        bad = replace(a, revision_id=unsafe)
        envelope = ProviderSearchResponse(
            results=[bad],
            public_notices=(PublicSearchNotice(bad),),
        )
        assert project_public_search_results(envelope) == {}
