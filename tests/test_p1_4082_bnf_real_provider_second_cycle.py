"""#4082: Core -> real BnF HTML/HTTPX provider manual second cycle.

Synthetic authenticated activity + HTTPX MockTransport only. No portal,
credential, remote POST, signed URL, ACSM retrieval or user-side mutation.
"""
from __future__ import annotations

import httpx

from cifereca.core.borrow_action_scope import borrow_claim_key
from tests.test_core_p0_2055_manual_dispatch import setup
from tests.test_core_p0_2055_bnf_real_provider_dispatch import (
    BnfProviderSession, CatalogRecord, ReaderHTTPClient, DETAIL, CONFIRM, RID,
)


def _activity(*loans):
    cards = "".join(
        '<div class="js-loan"><a href="/resources/' + resource + '">Synthetic title</a>'
        '<a href="/loans/' + ident + '">Open loan</a></div>'
        for ident, resource in loans
    )
    return '<section class="active-loans">' + cards + '</section>'


FIRST = _activity(("first-loan", RID), ("other-loan", "other-edition"))
OTHER = _activity(("other-loan", "other-edition"))
SECOND = _activity(("second-loan", RID), ("other-loan", "other-edition"))


def _service(tmp_path):
    service, _, account, _ = setup(tmp_path)
    state = {"post": 0, "activity": _activity(), "calls": []}

    def handler(req):
        assert req.url.host == "pret.bnf.fr"
        state["calls"].append((req.method, req.url.path))
        if req.method == "GET" and req.url.path == "/my_profile/activity":
            return httpx.Response(200, text=state["activity"],
                                  headers={"content-type": "text/html"})
        if req.method == "GET" and req.url.path == "/resources/" + RID:
            return httpx.Response(200, text=DETAIL,
                                  headers={"content-type": "text/html"})
        if (req.method == "GET"
                and req.url.path == "/resources/" + RID + "/loans/new"):
            return httpx.Response(200, text=CONFIRM,
                                  headers={"content-type": "text/html"})
        if req.method == "POST" and req.url.path == "/loans/activation":
            assert b"resource_id=synthetic-book" in req.content
            assert b"authenticity_token=synthetic-csrf" in req.content
            state["post"] += 1
            state["activity"] = FIRST if state["post"] == 1 else SECOND
            receipt = (
                '<a href="/loans/first-loan">Receipt</a>'
                if state["post"] == 1
                else '<a href="/loans/second-loan">Receipt</a>'
            )
            return httpx.Response(200, text=receipt,
                                  headers={"content-type": "text/html"})
        raise AssertionError("unexpected BnF request")

    client = ReaderHTTPClient(transport=httpx.MockTransport(handler))
    session = BnfProviderSession(
        client=client, credential_id=account.credential_id,
        label="Synthetic BnF", credentials={}, options={},
    )
    session._accounts = [account]
    session.connected = True
    item = session._search_result(CatalogRecord(RID, "Synthetic book"))
    service._sessions[service._session_key(
        "bnf-pret", account.credential_id,
    )] = session
    return service, session, account, item, state


def _click(service, account, item):
    return service.borrow_manual(
        item, expected_identity_id="default", confirmed=True,
        expected_context_epoch=service._account_context_epoch,
        expected_account=account,
    )


def test_real_bnf_provider_second_cycle_uses_fresh_absence_and_distinct_post(tmp_path):
    service, session, account, item, state = _service(tmp_path)
    try:
        first = _click(service, account, item)
        assert first["status"] == "confirmed"
        assert state["post"] == 1
        key = borrow_claim_key(account, item)
        previous = service.storage.metadata_get(key)
        assert service.storage.loan_borrow_state(key)["verified_loan_id"] == "first-loan"

        # A validated nonempty *fresh* borrower inventory still contains an
        # unrelated positive loan, but no longer the exact former edition.
        state["activity"] = OTHER
        previous_reads = len(state["calls"])
        assert service.can_borrow_manual(item) is True
        assert len(state["calls"]) == previous_reads  # passive UI never GETs
        second = _click(service, account, item)
        assert second["status"] == "confirmed"
        assert second["provider_verified"] is True
        assert state["post"] == 2
        row = service.storage.loan_borrow_state(key)
        assert row["cycle_generation"] == 2
        assert row["verified_loan_id"] == "second-loan"
        assert row["previous_operation_id"] != row["operation_id"]
        history = "loan.borrow.history.v1:" + key.split(":")[-1] + ":1"
        assert service.storage.metadata_get(history) == previous
        assert len([call for call in state["calls"]
                    if call == ("POST", "/loans/activation")]) == 2
    finally:
        service.close()


def test_real_bnf_provider_active_loan_never_starts_second_post(tmp_path):
    service, session, account, item, state = _service(tmp_path)
    try:
        assert _click(service, account, item)["status"] == "confirmed"
        assert state["post"] == 1
        key = borrow_claim_key(account, item)
        previous = service.storage.metadata_get(key)
        # Fresh official activity still contains the original loan.
        assert service.can_borrow_manual(item) is True
        before = len(state["calls"])
        denied = _click(service, account, item)
        assert denied["status"] == "blocked"
        assert denied["reason_code"] == "borrow_cycle_not_ended"
        assert state["post"] == 1
        assert state["calls"][before:] == [("GET", "/my_profile/activity")]
        assert service.storage.metadata_get(key) == previous
    finally:
        service.close()


def test_real_bnf_provider_empty_activity_never_forges_completed_cycle(tmp_path):
    service, session, account, item, state = _service(tmp_path)
    try:
        assert _click(service, account, item)["status"] == "confirmed"
        assert state["post"] == 1
        key = borrow_claim_key(account, item)
        previous = service.storage.metadata_get(key)
        # BnF currently does NOT qualify a zero-card active-loans section as
        # exhaustive. Do not turn an ambiguous empty widget into a new POST.
        state["activity"] = _activity()
        before = len(state["calls"])
        denied = _click(service, account, item)
        assert denied["reason_code"] == "borrow_cycle_not_ended"
        assert state["calls"][before:] == [("GET", "/my_profile/activity")]
        assert state["post"] == 1
        assert service.storage.metadata_get(key) == previous
    finally:
        service.close()
