"""Real Paris adapter/redirect parser, synthetic HTTP, no real borrow."""
from dataclasses import replace
from types import SimpleNamespace as NS
from urllib.parse import quote

import httpx
import pytest

from cifereca.plugin_api import (
    Account, SearchResult, LendingActionAdmission, ProviderCapabilityError,
    ProviderConfigurationError, BorrowRequestNotSubmitted,
)
from cifereca_paris import ParisProviderSession


ADMISSION = LendingActionAdmission("loan.borrow", "manual", "admitted", "admission_ready")
CLAIM = {"schema": 1, "operation": "loan.borrow", "status": "request_sent",
         "operation_id": "synthetic-core-claim"}


def setup(monkeypatch, *, result="acsm", existing=False):
    state = {"active": existing, "current": True, "owner": "identity-A"}
    calls = []
    session = ParisProviderSession(
        credential_id="card-A", label="Paris", credentials={}, options={},
        config={}, diagnostics=NS(add=lambda *a, **k: None),
    )
    account = Account(unique_id="", is_primary=True, provider_id="paris",
                      credential_id="card-A", identity_id="identity-A")
    session._accounts = [account]
    session.connected = True
    session.identity_broker = NS(accounts=lambda identity_id: [NS(
        provider_id="paris", credential_id="card-A", scope_version=2,
        account_id="paris:card-A:primary", identity_id=state["owner"],
    )])
    item = SearchResult(id="paris:edition", record_id="edition", title="Synthetic",
                        docbase="DILICOM", medium="digital",
                        source_provider="paris", source_credential="card-A",
                        author=None, type=None, format=None, publisher=None,
                        published_at=None, description=None, thumbnail_url=None,
                        friendly_url=None, scenario="EBOOKS")
    href = "/numerique/Portal/ASSARedirect.ashx?url=" + quote("/fake/loan-book.ashx", safe="")
    payload = {"d": {"ItemHoldingsData": {"CanLoan": True},
                     "HtmlView": f'<a href="{href}">Borrow</a><a href="{href}">Borrow</a>'}}
    def holdings(*args):
        calls.append("holdings")
        return payload
    monkeypatch.setattr(session.client, "_holdings_payload", holdings)

    def http(request):
        assert request.method == "GET"
        if request.url.path.endswith("ListLoans"):
            calls.append("loans")
            assert request.url.params.get("userUniqueIdentifier") == ""
            rows = [{"Id": "loan", "RecordId": "edition", "State": "active",
                     "WhenBack": "/Date(1794000000000)/"}] if state["active"] else []
            if state.get("duplicate"):
                rows *= 2
            return httpx.Response(200, json={"success": True, "d": {"Loans": rows}})
        assert request.url.path.endswith("ASSARedirect.ashx")
        calls.append("mutating-get")
        state["active"] = result not in ("unconfirmed", "timeout-unconfirmed")
        if result in ("timeout", "timeout-unconfirmed"):
            raise httpx.ReadTimeout("synthetic timeout", request=request)
        if result == "revoked":
            state["current"] = False
        if result == "no-acsm":
            return httpx.Response(200, text="loan created, delivery unavailable")
        return httpx.Response(302, headers={
            "Location": "https://bibliotheques.paris.fr/numerique/synthetic.acsm",
        })

    session.client.client.close()
    session.client.client = httpx.Client(transport=httpx.MockTransport(http),
                                       follow_redirects=False, trust_env=False)
    def run(**kwargs):
        return session.borrow_explicit_with_claim(
            item, account, admission=ADMISSION, claimed_receipt=CLAIM,
            confirmed=True, core_scope_current=lambda: state["current"], **kwargs,
        )
    return session, account, item, state, calls, run


@pytest.mark.parametrize("result", ["acsm", "no-acsm", "timeout"])
def test_one_click_can_confirm_loan_without_initial_acsm(monkeypatch, result):
    session, account, _, _, calls, run = setup(monkeypatch, result=result)
    try:
        loan = run()
        assert (loan.id, loan.record_id, loan.source_account_id, loan.identity_id) == (
            "loan", "edition", "primary", "identity-A")
        assert loan.due_at is not None
        assert bool(loan.ebook_url) is (result == "acsm")
        assert calls == ["loans", "holdings", "mutating-get", "loans"]
        with pytest.raises(ProviderCapabilityError, match="claim_spent"):
            run()
        assert len(calls) == 4
    finally:
        session.close()


def test_existing_loan_never_repeats_borrow_or_download(monkeypatch):
    session, _, _, _, calls, run = setup(monkeypatch, existing=True)
    try:
        assert run().id == "loan"
        assert calls == ["loans"]
        assert session._borrow_claim_attempts == set()
    finally:
        session.close()


@pytest.mark.parametrize("result", ["unconfirmed", "timeout-unconfirmed"])
def test_unconfirmed_attempt_is_not_success_or_retry(monkeypatch, result):
    session, _, _, _, calls, run = setup(monkeypatch, result=result)
    try:
        with pytest.raises(ProviderCapabilityError, match="result_unverified"):
            run()
        with pytest.raises(ProviderCapabilityError, match="claim_spent"):
            run()
        assert calls.count("mutating-get") == 1
    finally:
        session.close()


@pytest.mark.parametrize("change", ["identity", "credential", "account", "owner", "lease"])
def test_changed_scope_during_holdings_never_submits(monkeypatch, change):
    session, account, _, state, calls, run = setup(monkeypatch)
    original = session.client._holdings_payload
    def changed(*args):
        payload = original(*args)
        if change == "identity":
            account.identity_id = "identity-B"
        elif change == "credential":
            account.credential_id = "card-B"
        elif change == "account":
            session._accounts = [replace(account)]
        elif change == "owner":
            state["owner"] = "identity-B"
        else:
            state["current"] = False
        return payload
    monkeypatch.setattr(session.client, "_holdings_payload", changed)
    try:
        with pytest.raises(BorrowRequestNotSubmitted):
            run()
        assert calls == ["loans", "holdings"]
        assert session._borrow_claim_attempts == set()
    finally:
        session.close()


def test_revocation_during_first_mutating_get_stops_confirmation_and_keeps_claim(monkeypatch):
    session, _, _, state, calls, run = setup(monkeypatch, result="revoked")
    try:
        with pytest.raises(ProviderConfigurationError, match="scope_changed"):
            run()
        assert calls == ["loans", "holdings", "mutating-get"]
        assert session._borrow_claim_attempts == {CLAIM["operation_id"]}
        state["current"] = True
        with pytest.raises(ProviderCapabilityError, match="claim_spent"):
            run()
        assert len(calls) == 3
    finally:
        session.close()


@pytest.mark.parametrize("change", ["linked", "foreign", "duplicate", "no_owner"])
def test_ineligible_or_ambiguous_scope_refuses_before_http(monkeypatch, change):
    session, account, _, _, calls, run = setup(monkeypatch)
    if change == "linked":
        account.unique_id = "linked"
    elif change == "foreign":
        account.identity_id = "identity-B"
    elif change == "duplicate":
        session._accounts.append(replace(account))
    else:
        session.identity_broker = None
    try:
        with pytest.raises(ProviderConfigurationError):
            run()
        assert calls == []
    finally:
        session.close()


def test_legacy_borrow_has_no_permission_to_dispatch(monkeypatch):
    session, account, item, _, calls, _ = setup(monkeypatch)
    try:
        with pytest.raises(ProviderCapabilityError, match="claim_required"):
            session.borrow(item, account)
        assert calls == []
    finally:
        session.close()


def test_duplicate_exact_loans_are_not_arbitrarily_confirmed(monkeypatch):
    session, _, _, state, calls, run = setup(monkeypatch, existing=True)
    state["duplicate"] = True
    try:
        with pytest.raises(BorrowRequestNotSubmitted):
            run()
        assert calls == ["loans"]
    finally:
        session.close()


@pytest.mark.parametrize("field,value", [
    ("schema", 2), ("operation", "loan.renew"), ("status", "confirmed"),
    ("operation_id", ""), ("operation_id", None),
])
def test_invalid_core_receipt_never_reaches_private_transport(monkeypatch, field, value):
    session, account, item, _, calls, _ = setup(monkeypatch)
    try:
        with pytest.raises(ProviderCapabilityError, match="not_authorized"):
            session.borrow_explicit_with_claim(
                item, account, admission=ADMISSION,
                claimed_receipt={**CLAIM, field: value}, confirmed=True,
                core_scope_current=lambda: True,
            )
        assert calls == []
    finally:
        session.close()


def test_no_explicit_confirmation_never_reaches_private_transport(monkeypatch):
    session, account, item, _, calls, _ = setup(monkeypatch)
    try:
        with pytest.raises(ProviderCapabilityError, match="not_authorized"):
            session.borrow_explicit_with_claim(
                item, account, admission=ADMISSION, claimed_receipt=CLAIM,
                confirmed=False, core_scope_current=lambda: True,
            )
        assert calls == []
    finally:
        session.close()


def test_revocation_between_redirect_hops_prevents_next_private_get(monkeypatch):
    session, _, _, state, calls, run = setup(monkeypatch)
    actual = session.client._get_pnb_response
    def first_hop(url, **kwargs):
        # The real transport sees the live callback before dispatch.
        response = actual(url, **kwargs)
        assert response.status_code == 302
        state["current"] = False
        return httpx.Response(302, request=response.request, headers={
            "Location": "https://bibliotheques.paris.fr/numerique/next-hop",
        })
    monkeypatch.setattr(session.client, "_get_pnb_response", first_hop)
    try:
        with pytest.raises(ProviderConfigurationError, match="scope_changed"):
            run()
        assert calls == ["loans", "holdings", "mutating-get"]
        assert session._borrow_claim_attempts == {CLAIM["operation_id"]}
    finally:
        session.close()
