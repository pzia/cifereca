"""#4030 reception: public, private cache and typed loan source coexist.

Real Core broker, one verified EPUB/Placement and SQLite; no second writer,
implicit lookup, synthetic OriginLink, right or download. Reuse individual
negative matrices rather than re-implementing source contracts here.
"""
from dataclasses import replace
from types import SimpleNamespace as NS

import httpx
import pytest

from cifereca.core import local_origin_remote_loans as remote
from cifereca.core.local_origin_detail_broker import LocalOriginDetailReadFactory
from cifereca.plugin_api.loan_inventory import (
    LoanInventoryScope, LoanInventorySnapshot, LoanBranchCoverage,
)
from cifereca.plugin_api import ProviderSearchResponse
from tests.test_core_p0_4030_private_catalogue import private_rig
from tests.test_core_p0_4030_public_catalogue import rig, store, rows


@pytest.mark.parametrize("public_outcome", [
    "matches", "http_401", "http_403", "http_429", "timeout",
    "empty_partial", "empty_declared_complete", "empty_unverified",
])
def test_three_independent_explicit_catalogue_surfaces_keep_one_readonly_authority(
    private_rig, tmp_path, monkeypatch, public_outcome,
):
    service, request, account, session, calls, path, _, _ = private_rig
    public_session = service._sessions["public"]
    service._provider_enabled_for_identity = (
        lambda provider, identity: identity == "A"
        and provider in {"new-catalogue", "seller"}
    )
    # A typed remote inventory is a separate source even when the same
    # account already has one exact loan reference in the local cache.
    session.capabilities = NS(loans=True)
    observed = replace(service.last_loans[0], id="fresh-loan")
    account_reads = []

    def inventory(selected):
        assert selected is account
        account_reads.append(selected)
        return LoanInventorySnapshot(
            LoanInventoryScope.for_account(account), (observed,),
            status="success",
            branches=(LoanBranchCoverage("loans", "digital", "complete", True),),
            observed_at="2026-01-01T00:00:00Z",
        )

    session.loan_inventory_snapshot = inventory
    monkeypatch.setattr(remote, "acquisition_lock_file", lambda: tmp_path / "acquire.lock")
    if public_outcome.startswith("http_") or public_outcome == "timeout":
        def denied(title, scope, **kwargs):
            calls.append((title, scope, kwargs))
            req = httpx.Request("GET", "https://example.invalid/catalogue")
            if public_outcome == "timeout":
                raise httpx.ReadTimeout("PRIVATE fixture timeout", request=req)
            raise httpx.HTTPStatusError(
                "PRIVATE fixture denied", request=req,
                response=httpx.Response(int(public_outcome[5:]), request=req),
            )
        public_session.search_with_response_facets = denied
    elif public_outcome.startswith("empty_"):
        def empty(title, scope, **kwargs):
            calls.append((title, scope, kwargs))
            return ProviderSearchResponse(
                [], partial=public_outcome == "empty_partial",
                catalogue_complete={"empty_partial": False,
                                    "empty_declared_complete": True,
                                    "empty_unverified": None}[public_outcome],
            )
        public_session.search_with_response_facets = empty

    before, contents = rows(service.storage), path.read_bytes()
    broker = LocalOriginDetailReadFactory(service).for_activation(
        "fixture", current=lambda: True,
    )
    page = broker.preview(
        identity_id=request.identity_id, placement_id=request.placement_id,
        still_current=lambda: True, confirmed=True,
    )
    assert page is not None and page.metadata_evidence == "verified_epub_opf_description"
    (public,), (private,), (remote_choice,) = (
        page.public_choices, page.private_choices, page.remote_loan_choices,
    )
    assert public.provider_id == "new-catalogue"
    assert (private.provider_id, private.kind) == ("seller", "loan")
    assert remote_choice.provider_id == "seller"
    assert not calls and not account_reads
    assert not page.provider_access_performed and not page.association_authorized
    assert broker.search_public(token=public.token) is None
    assert broker.compare_private(token=private.token) is None
    assert broker.compare_remote_loan(token=remote_choice.token) is None
    assert not calls and not account_reads

    public_report = broker.search_public(token=public.token, confirmed=True)
    assert public_report is not None and public_report.provider_access_performed
    if public_outcome == "matches":
        assert public_report.status == "partial"
        assert [(row.record_id, row.comparison) for row in public_report.matches] == [
            ("one", "isbn_claim_ambiguous"),
            ("two", "isbn_claim_ambiguous"),
        ]
    elif public_outcome.startswith("empty_"):
        assert public_report.status == "partial"
        assert public_report.matches == ()
        assert public_report.window_status == {
            "empty_partial": "incomplete",
            "empty_declared_complete": "declared_complete",
            "empty_unverified": "unverified",
        }[public_outcome]
    else:
        assert public_report.status == {
            "http_401": "auth_required", "http_403": "access_denied",
            "http_429": "rate_limited", "timeout": "timeout",
        }[public_outcome]
        assert public_report.matches == ()
        assert public_report.window_status == "unverified"
    assert broker.search_public(token=public.token, confirmed=True) is None

    # A public failure or empty window is not a veto on independently admitted local cache
    # comparison or the user's explicit read of one account's typed loans.
    private_report = broker.compare_private(token=private.token, confirmed=True)
    assert private_report is not None and private_report.status == "partial"
    known, = private_report.matches
    assert known.comparison == "title_author_concordant"
    assert known.evidence == "cached_loan_description"
    assert not private_report.provider_access_performed
    assert broker.compare_private(token=private.token, confirmed=True) is None

    remote_report = broker.compare_remote_loan(
        token=remote_choice.token, confirmed=True,
    )
    assert remote_report is not None and remote_report.status == "partial"
    fresh, = remote_report.matches
    assert fresh.loan_id == "fresh-loan" and fresh.edition_id == observed.record_id
    assert not hasattr(fresh, "source") and not hasattr(fresh, "branch")
    assert remote_report.provider_read_requested
    assert not remote_report.local_description_transmitted
    assert broker.compare_remote_loan(token=remote_choice.token, confirmed=True) is None

    assert len(calls) == 1 and calls[0] == (
        "Roman commun", "digital", {"field": "title", "content_types": ()},
    )
    assert account_reads == [account]
    for report in (public_report, private_report, remote_report):
        assert not report.coverage_complete and not report.fresh_network_verified
        assert not report.association_authorized
    assert rows(service.storage) == before and path.read_bytes() == contents


@pytest.mark.parametrize("surface", ["public", "private", "remote"])
def test_three_surface_choices_are_revoked_together_on_profile_epoch_change(
    private_rig, tmp_path, monkeypatch, surface,
):
    service, request, account, session, calls, path, _, _ = private_rig
    service._provider_enabled_for_identity = (
        lambda provider, identity: identity == "A"
        and provider in {"new-catalogue", "seller"}
    )
    session.capabilities = NS(loans=True)
    calls_remote = []
    session.loan_inventory_snapshot = lambda account: calls_remote.append(account)
    monkeypatch.setattr(remote, "acquisition_lock_file", lambda: tmp_path / "acquire.lock")
    broker = LocalOriginDetailReadFactory(service).for_activation(
        "fixture", current=lambda: True,
    )
    page = broker.preview(
        identity_id=request.identity_id, placement_id=request.placement_id,
        still_current=lambda: True, confirmed=True,
    )
    assert page is not None and all((
        page.public_choices, page.private_choices, page.remote_loan_choices,
    ))
    before, contents = rows(service.storage), path.read_bytes()
    service._account_context_epoch += 1
    tokens = {
        "public": page.public_choices[0].token,
        "private": page.private_choices[0].token,
        "remote": page.remote_loan_choices[0].token,
    }
    if surface == "public":
        result = broker.search_public(token=tokens[surface], confirmed=True)
    elif surface == "private":
        result = broker.compare_private(token=tokens[surface], confirmed=True)
    else:
        result = broker.compare_remote_loan(token=tokens[surface], confirmed=True)
    assert result is None
    assert not calls and not calls_remote
    assert rows(service.storage) == before and path.read_bytes() == contents
