"""#6366: federation must not scan 10k PublicSearchNotice claims per row.

The index is per *same supplier response*, never a durable source cache or a
new public-attestation authority. Prove exact behavior and linear traversal
without fragile timing assertions.
"""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace as NS

from cifereca.core import public_search_projection as projection
from cifereca.core.search_orchestration import _public_rows_from_response
from cifereca.plugin_api import ProviderSearchResponse, PublicSearchNotice
from tests.test_core_p0_6366_real_search_notice_c_ingestion import public_row, URL


def public_session():
    return NS(
        provider_id="bnf", credential_id="__public__",
        credentials={}, _accounts=[],
    )


def responses(count):
    source = public_row()
    rows = [
        replace(
            source, id=f"public-row-{index}",
            record_id=f"bnf-record-{index}",
            metadata={"languages": ["fr"], "account": "private-untrusted"},
        )
        for index in range(count)
    ]
    return ProviderSearchResponse(
        results=rows,
        public_notices=tuple(PublicSearchNotice(result=row) for row in rows),
    )


def test_1200_public_rows_are_validated_in_linear_source_traversal(
    monkeypatch,
):
    response = responses(1200)
    calls = []
    original = projection._key

    def measured(source):
        calls.append(1)
        return original(source)

    monkeypatch.setattr(projection, "_key", measured)
    rows = _public_rows_from_response(response, public_session())
    assert len(rows) == 1200
    assert len(calls) <= 2 * 1200 + 8  # No N × N repeated projection.
    assert len({row.record_id for row in rows}) == 1200
    assert all(row.thumbnail_url == URL for row in rows)
    assert all(row.metadata == {
        "catalogue_access": "shared_read_only", "languages": ["fr"],
    } for row in rows)
    assert all(row.owned is False and row.downloadable is False for row in rows)

    # Generic and batched projections must produce precisely the same
    # sanitized payload, without a plugin-specific field whitelist.
    subset = projection.project_public_search_results(response)
    assert subset is not None and len(subset) == 1200
    for item in response.results[::311]:
        assert subset[id(item)] == projection.project_public_search_result(
            response, item, provider_id="bnf", credential_id="__public__",
        )


def test_bad_declaration_schema_or_source_invalidates_entire_response():
    source = public_row()
    for entry in (
        PublicSearchNotice(source, schema_version=2),
        object(),
        PublicSearchNotice(replace(source, record_id="\x00invalid")),
    ):
        response = ProviderSearchResponse(
            results=[source], public_notices=(entry,),
        )
        assert projection.project_public_search_results(response) is None
        assert _public_rows_from_response(response, public_session()) == []


def test_duplicate_notices_are_ambiguous_and_cannot_publish_an_image():
    source = public_row()
    response = ProviderSearchResponse(
        results=[source],
        public_notices=(PublicSearchNotice(source),
                        PublicSearchNotice(replace(source))),
    )
    assert projection.project_public_search_results(response) == {}
    assert _public_rows_from_response(response, public_session()) == []


def test_private_claim_or_cross_call_copy_not_promoted_to_anonymous_image():
    source = public_row()
    copied = replace(source)
    copied_private = replace(source, source_account_id="private-account")
    for claim in (copied, copied_private):
        response = ProviderSearchResponse(
            results=[source], public_notices=(PublicSearchNotice(claim),),
        )
        out = _public_rows_from_response(response, public_session())
        assert all(row.thumbnail_url is None for row in out)
        assert all(row.download_url is None and row.owned is False for row in out)
        # Direct and batch sanitizers agree on the same-call declaration.
        indexed = projection.project_public_search_results(response)
        assert indexed is not None
        if indexed:
            assert indexed[id(source)] == projection.project_public_search_result(
                response, source,
                provider_id="bnf", credential_id="__public__",
            )
