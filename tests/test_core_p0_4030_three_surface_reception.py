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


@pytest.mark.parametrize("failure", [401, 403, 429, "timeout"])
def test_public_failure_then_paged_private_declaration_and_revocation(
    private_rig, tmp_path, monkeypatch, failure,
):
    """One operator flow: refusal is not absence, declaration is not origin."""
    from cifereca.core import library_declared_links as engine
    from cifereca.core import local_origin_declaration_bridge as bridge
    from cifereca.core import local_origin_private_catalogue as private
    from cifereca.core.activation_scoped_broker import ActivationBrokerResolver
    from cifereca.core.local_origin_detail_broker import take_native_local_origin_declaration
    from tests.test_core_p0_4030_private_catalogue import _populate_windows
    from tests.test_library_declared_links import records, rows as table_rows

    service, request, account, session, calls, path, key, _ = private_rig
    service.identities = [service.current_identity]
    service.registry = object()
    service.config.data = {}
    service._provider_enabled_for_identity = lambda p, i: i == "A" and p in (
        "new-catalogue", "seller")
    monkeypatch.setattr(engine, "acquisition_lock_file", lambda: tmp_path / "declare.lock")
    _populate_windows(private_rig)
    monkeypatch.setattr(private, "_MAX_REFERENCES", 2)
    service.storage.entitlement_put(dict(
        entitlement_id="known-loan", identity_id="A", provider_id="seller",
        credential_id="card-A", account_id="buyer-A", item_id=key.edition_id,
        entitlement_type="loan", state="active", loan_id=key.right_id,
    ))

    def denied(title, scope, **kwargs):
        calls.append((title, scope, kwargs))
        req = httpx.Request("GET", "https://example.invalid/catalogue")
        if failure == "timeout":
            raise httpx.ReadTimeout("PRIVATE fixture timeout", request=req)
        raise httpx.HTTPStatusError(
            "PRIVATE fixture refusal", request=req,
            response=httpx.Response(failure, request=req),
        )

    service._sessions["public"].search_with_response_facets = denied
    names = ("entitlements", "source_references", "origin_links", "artifacts",
             "placements", "catalog_entries", "acquisition_ledger")
    before = {name: table_rows(service, name) for name in names}
    contents = path.read_bytes()
    port = "library.origin.detail.read"
    factory = LocalOriginDetailReadFactory(service)
    resolver = ActivationBrokerResolver(lambda _: None, lambda _: factory, "fixture", (port,))
    broker = resolver(port)
    plugin = object()
    resolved = NS(plugin_id="fixture", service=plugin, registry_generation=3)
    manager = NS(_registry_generation=3, service=lambda _: plugin,
                 _records={"fixture": NS(service=plugin, activation_brokers=resolver)})
    page = broker.preview(identity_id="A", placement_id=request.placement_id,
                          still_current=lambda: True, confirmed=True)
    public, = page.public_choices
    loan, = page.private_choices
    assert not calls and not records(service)
    failed = broker.search_public(token=public.token, confirmed=True)
    assert failed.status == {401: "auth_required", 403: "access_denied",
                             429: "rate_limited", "timeout": "timeout"}[failure]
    assert failed.matches == () and not failed.coverage_complete
    assert not failed.fresh_network_verified and not failed.association_authorized
    assert broker.search_public(token=public.token, confirmed=True) is None

    first = broker.compare_private(token=loan.token, confirmed=True)
    assert first.status == "partial" and first.page == 1 and first.window_truncated
    assert [m.resource_id for m in first.matches] == ["loan-1", "loan-2"]
    assert not first.fresh_network_verified and not first.coverage_complete
    assert not first.provider_access_performed and not first.association_authorized
    cursor = first.next_token
    assert cursor and not records(service)
    hit = first.matches[0]
    advice = broker.preview_loan_declaration(token=hit.declaration_choice, confirmed=True)
    assert advice is not None and advice.trust == "user_declaration"
    assert not advice.association_authorized and not records(service)
    pending = take_native_local_origin_declaration(
        manager, resolved, advice, service=service, identity_id="A",
        placement_id=request.placement_id,
    )
    assert pending is not None
    preview, current = pending
    assert bridge.admit_private_loan_declaration(service, preview) is None
    command = bridge.admit_private_loan_declaration(
        service, preview, user_confirmed=True, still_current=current,
    )
    assert callable(command)
    result = command()
    assert result.status == "declared" and result.changed == 1
    assert command() is None
    record, = records(service)
    assert record["origin"] == "user_declaration"
    # The previous page's SQLite witness cannot survive this durable write.
    stale = broker.compare_private(token=cursor, confirmed=True)
    assert stale is None or (stale.status != "partial" and not stale.matches)
    undo = engine.withdraw_declared_link(
        service, identity_id="A", relation_id=result.relation_id,
    )
    assert undo.status == "revoke_preview"
    assert engine.withdraw_declared_link(
        service, identity_id="A", relation_id=result.relation_id,
        confirmation=undo.plan_token,
    ).status == "revoked"
    assert records(service)[0]["state"] == "revoked"
    assert len(calls) == 1 and calls[0] == (
        "Roman commun", "digital", {"field": "title", "content_types": ()},
    )
    assert {name: table_rows(service, name) for name in names} == before
    assert path.read_bytes() == contents
