"""#4157: Paris ListLoans branch-coverage acceptance, synthetic HTTP only.

These fixtures pin successful per-branch coverage, account scope and
failure isolation. They never contact Paris, ADEPT,
a lending endpoint or a storage backend. A successful ListLoans read is not
proof that an EPUB was acquired or a Placement was published.
"""
from __future__ import annotations

from dataclasses import replace

import httpx
import pytest

from cifereca.plugin_api import ProviderConfigurationError
from tests.test_p1_3222_paris_listloans_http import _fixture

UPSTREAMS = {"DILICOM": ("dilicom", "digital"),
             "SYRACUSE": ("syracuse", "physical")}
PRIVATE = "TEST_PRIVATE_CREDENTIAL_4157"


def loan_row(label: str) -> dict:
    # The values are invented and never correspond to a real reader or title.
    return {"Id": "synthetic-" + label, "RecordId": "synthetic-edition-" + label,
            "Title": "Synthetic title " + label}


def reply(rows=(), *, extra: dict | None = None) -> httpx.Response:
    envelope = {"Loans": list(rows)}
    if extra:
        envelope.update(extra)
    return httpx.Response(200, json={"success": True, "d": envelope})


def scenario(*, digital=(), physical=(), digital_response=None,
             physical_response=None, borrower="linked-fixture"):
    """One session, two exact serviceCode GETs, without upstream I/O."""
    calls = []
    answers = {
        "DILICOM": digital_response if digital_response is not None else reply(digital),
        "SYRACUSE": physical_response if physical_response is not None else reply(physical),
    }

    def handler(request):
        assert request.method == "GET"
        assert request.url.path.endswith("/ListLoans")
        service = request.url.params.get("serviceCode")
        assert service in UPSTREAMS
        assert request.url.params.get("userUniqueIdentifier") == borrower
        calls.append((service, borrower))
        return answers[service]

    session, account, diagnostics = _fixture(handler)
    account.unique_id = borrower
    session._enrich_digital_loan_metadata = lambda loan: None
    return session, account, diagnostics, calls


@pytest.mark.parametrize(("digital_count", "physical_count"), [
    (0, 0), (1, 0), (0, 1), (3, 2),
])
@pytest.mark.parametrize("borrower", ["", "linked-fixture"])
def test_synthetic_zero_one_many_positive_loans_remain_exactly_scoped(
    digital_count, physical_count, borrower,
):
    digital = tuple(loan_row("digital-" + str(n)) for n in range(digital_count))
    physical = tuple(loan_row("physical-" + str(n)) for n in range(physical_count))
    session, account, _, calls = scenario(
        digital=digital, physical=physical, borrower=borrower,
    )
    try:
        snapshot = session.loan_inventory_snapshot(account)
        assert snapshot.status == "success"
        assert snapshot.scope.account_id == borrower
        assert snapshot.scope.identity_id == account.identity_id
        assert snapshot.scope.credential_id == account.credential_id
        assert len(snapshot.loans) == digital_count + physical_count
        assert sum(x.medium == "digital" for x in snapshot.loans) == digital_count
        assert sum(x.medium == "physical" for x in snapshot.loans) == physical_count
        assert all(x.identity_id == account.identity_id for x in snapshot.loans)
        assert all(x.source_credential == account.credential_id for x in snapshot.loans)
        assert all(x.source_account_id == (borrower or "primary")
                   for x in snapshot.loans)
        assert calls == [("DILICOM", borrower), ("SYRACUSE", borrower)]
        assert [(c.upstream, c.medium) for c in snapshot.branches] == [
            ("dilicom", "digital"), ("syracuse", "physical"),
        ]
        for coverage in snapshot.branches:
            assert coverage.completeness == "complete"
            assert coverage.pagination_complete is True
    finally:
        session.close()


@pytest.mark.parametrize(("digital_count", "physical_count"), [
    (0, 0), (1, 0), (2, 1),
])
def test_successful_official_read_attests_each_branch(
    digital_count, physical_count,
):
    session, account, _, calls = scenario(
        digital=tuple(loan_row(f"d{n}") for n in range(digital_count)),
        physical=tuple(loan_row(f"p{n}") for n in range(physical_count)),
    )
    try:
        snapshot = session.loan_inventory_snapshot(account)
        assert snapshot.status == "success"
        assert len(snapshot.loans) == digital_count + physical_count
        assert len(calls) == 2
        assert snapshot.branch_claims_complete("dilicom", "digital") is True
        assert snapshot.branch_claims_complete("syracuse", "physical") is True
    finally:
        session.close()


@pytest.mark.parametrize("failure", [
    "unauthorized", "timeout", "html", "missing_loans",
    "null_loans", "wrong_shape", "redirect",
])
@pytest.mark.parametrize("failed_upstream", ["DILICOM", "SYRACUSE"])
def test_failed_branch_never_attests_absence_or_hides_successful_sibling(
    failure, failed_upstream,
):
    calls = []

    def handler(request):
        service = request.url.params.get("serviceCode")
        calls.append((service, request.url.path))
        if request.url.path == "/login":
            return reply((loan_row("wrong-login"),))
        if service != failed_upstream:
            return reply((loan_row("good-" + service),))
        if failure == "unauthorized":
            return httpx.Response(401, text="PRIVATE " + PRIVATE)
        if failure == "timeout":
            raise httpx.ConnectTimeout(PRIVATE, request=request)
        if failure == "html":
            return httpx.Response(
                200, text="<html>" + PRIVATE + "</html>",
                headers={"content-type": "text/html"},
            )
        if failure == "missing_loans":
            return httpx.Response(200, json={"success": True, "d": {}})
        if failure == "null_loans":
            return httpx.Response(200, json={"success": True, "d": {"Loans": None}})
        if failure == "wrong_shape":
            return httpx.Response(
                200, json={"success": True, "d": {"Loans": ["not-a-row"]}},
            )
        return httpx.Response(302, headers={"location": "/login"})

    session, account, diagnostics = _fixture(handler)
    account.unique_id = "linked-fixture"
    session._enrich_digital_loan_metadata = lambda loan: None
    try:
        result = session.loan_inventory_snapshot(account)
        assert result.status == "partial"
        assert result.reason_code == "branch_unavailable"
        assert len(result.loans) == 1
        assert all(x.source_account_id == account.unique_id for x in result.loans)
        coverage = {c.upstream: c for c in result.branches}
        failed = coverage[UPSTREAMS[failed_upstream][0]]
        successful = coverage[
            "syracuse" if failed_upstream == "DILICOM" else "dilicom"
        ]
        assert failed.completeness == "partial"
        assert failed.reason_code == "branch_unavailable"
        assert failed.pagination_complete is not True
        assert successful.completeness == "complete"
        assert successful.pagination_complete is True
        assert not result.branch_claims_complete(
            UPSTREAMS[failed_upstream][0], UPSTREAMS[failed_upstream][1],
        )
        assert sum(p.endswith("/ListLoans") for _, p in calls) == 2
        assert len(calls) <= 3  # one optional followed redirect, zero retries
        assert PRIVATE not in repr(result)
    finally:
        session.close()


@pytest.mark.parametrize("extra", [
    {"HasMore": True},
    {"HasMorePages": True},
    {"NextPage": 2},
    {"Next": "https://private.invalid/?token=TEST_PRIVATE_CREDENTIAL_4157"},
    {"ContinuationToken": "PRIVATE_TOKEN"},
    {"TotalCount": 10, "PageSize": 1},
])
def test_pagination_ambiguity_cannot_be_promoted_to_complete_inventory(extra):
    # The client rejects the unfinished branch before positive certification.
    session, account, _, calls = scenario(
        digital_response=reply((loan_row("d1"),), extra=extra),
    )
    try:
        result = session.loan_inventory_snapshot(account)
        assert not result.branch_claims_complete("dilicom", "digital")
        assert calls == [
            ("DILICOM", "linked-fixture"), ("SYRACUSE", "linked-fixture"),
        ]
    finally:
        session.close()


@pytest.mark.parametrize("bad_rows", [
    [{"RecordId": "edition", "Title": "Missing loan ID"}],
    [{"Id": "loan", "Title": "Missing edition ID"}],
    [{"Id": True, "RecordId": "edition"}],
    [{"Id": "loan", "RecordId": False}],
    [{"Id": "loan", "RecordId": ""}],
    [loan_row("duplicate"), loan_row("duplicate")],
    [loan_row("duplicate"), {**loan_row("duplicate"), "RecordId": "other"}],
])
@pytest.mark.parametrize("bad_upstream", ["DILICOM", "SYRACUSE"])
def test_invalid_or_ambiguous_row_never_certifies_missing_loans(bad_rows, bad_upstream):
    options = {"digital": (loan_row("good-d"),),
               "physical": (loan_row("good-p"),)}
    options["digital_response" if bad_upstream == "DILICOM" else "physical_response"] = reply(bad_rows)
    session, account, _, calls = scenario(**options)
    try:
        result = session.loan_inventory_snapshot(account)
        assert result.status == "partial"
        assert result.reason_code == "invalid_response"
        coverage = {b.upstream: b for b in result.branches}
        failed = coverage[bad_upstream.lower()]
        sibling = coverage["syracuse" if bad_upstream == "DILICOM" else "dilicom"]
        assert failed.completeness == "partial"
        assert failed.reason_code == "invalid_response"
        assert failed.pagination_complete is not True
        assert sibling.completeness == "complete" and sibling.pagination_complete is True
        assert len(result.loans) == len(bad_rows) + 1  # positive/descriptive rows retained
        assert len(calls) == 2
    finally:
        session.close()


def test_later_empty_read_cannot_reuse_previous_coverage_or_rows():
    session, account, _, calls = scenario(digital=(loan_row("d"),))
    try:
        first = session.loan_inventory_snapshot(account)
        assert first.status == "success" and len(first.loans) == 1
        old_send = session.client.client.send
        def empty_send(request, **kwargs):
            if request.url.params.get("serviceCode") == "DILICOM":
                # Still execute the same transport to account for the read.
                response = old_send(request, **kwargs)
                return httpx.Response(200, request=response.request,
                                      json={"success": True, "d": {"Loans": []}})
            return old_send(request, **kwargs)
        session.client.client.send = empty_send
        second = session.loan_inventory_snapshot(account)
        assert second.status == "success" and second.loans == ()
        assert all(b.completeness == "complete" and b.pagination_complete is True
                   for b in second.branches)
        assert len(first.loans) == 1 and len(calls) == 4
    finally:
        session.close()


@pytest.mark.parametrize("change", ["identity", "credential", "reader", "close"])
def test_mid_first_branch_account_mutation_blocks_second_private_get(change):
    requests = []
    state = {}

    def handler(request):
        requests.append(request.url.params.get("serviceCode"))
        if len(requests) == 1:
            account = state["account"]
            session = state["session"]
            if change == "identity":
                account.identity_id = "different-identity"
            elif change == "credential":
                account.credential_id = "different-credential"
            elif change == "reader":
                account.unique_id = "another-reader"
            else:
                # Closing the live session must invalidate the publisher
                # before another private GET.
                session.connected = False
        return reply((loan_row("first"),))

    session, account, _ = _fixture(handler)
    state.update(session=session, account=account)
    session._enrich_digital_loan_metadata = lambda loan: None
    try:
        with pytest.raises(ProviderConfigurationError, match="account_scope_mismatch"):
            session.loan_inventory_snapshot(account)
        assert requests == ["DILICOM"]
    finally:
        session.close()


def test_cross_credential_or_rebound_reader_is_rejected_without_any_get():
    session, account, _, calls = scenario()
    try:
        bad = replace(account, credential_id="foreign-credential")
        with pytest.raises(ProviderConfigurationError, match="account_scope_mismatch"):
            session.loan_inventory_snapshot(bad)
        assert calls == []
    finally:
        session.close()
