"""P1 #3122: real Paris/BnF adapters, one bounded synthetic inventory read.

No live HTTP, credential, ACSM, ADEPT or acquisition. Both adapters keep their
v1 loans(account) entrypoint and publish no negative rights evidence.
"""
from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace as NS

import pytest

ROOT = Path(__file__).resolve().parents[1]
for directory in (ROOT / "src/plugins/provider-paris",
                  ROOT / "src/plugins/provider-bnf-pret"):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from cifereca.models import Account, Loan
from cifereca.plugin_api import read_loan_inventory
from cifereca_paris import ParisProviderSession
from cifereca_bnf_pret import BnfProviderSession

BNF_HTML = (
    '<section class="active-loans">'
    '<div class="js-loan">'
    '<a href="/resources/book-a">Synthetic novel</a>'
    '<a href="/loans/loan-a">Private token action</a>'
    '</div></section>'
)


class FakeParisClient:
    def __init__(self):
        self.calls = []
        self.fail = set()

    def loans(self, upstream, borrower):
        self.calls.append((upstream, borrower))
        if upstream in self.fail:
            raise RuntimeError("synthetic upstream failure")
        return [Loan(
            id=f"{upstream}-{borrower or 'primary'}",
            provider=upstream,
            medium="digital" if upstream == "DILICOM" else "physical",
            record_id=f"record-{upstream}", title=f"Title {upstream}",
            source_account_id=None, identity_id=None,
        )]

    def close(self):
        pass


class FakeDiagnostics:
    def add(self, *args, **kwargs):
        pass


def paris(accounts):
    s = ParisProviderSession(
        credential_id="card-a", label="Paris", credentials={},
        options={}, config={}, diagnostics=FakeDiagnostics(),
    )
    s.client.close()
    s.client = FakeParisClient()
    s._enrich_digital_loan_metadata = lambda _row: None
    s.connected = True
    s._accounts = list(accounts)
    return s


def account(*, borrower="", identity="alice"):
    return Account(provider_id="paris", credential_id="card-a",
                   unique_id=borrower, identity_id=identity,
                   is_primary=not bool(borrower))


class FakeBnfClient:
    def __init__(self, html=BNF_HTML):
        self.html = html
        self.calls = []

    def login(self, username, password):
        self.calls.append("login")
        return self.html

    def activity(self):
        self.calls.append("activity")
        return self.html

    def clear(self):
        self.calls.append("clear")

    def close(self):
        self.calls.append("close")


def bnf(html=BNF_HTML):
    client = FakeBnfClient(html)
    s = BnfProviderSession(
        client=client, credential_id="bnf-card", label="BnF",
        credentials={"username": "synthetic", "password": "synthetic"},
        options={},
    )
    a, = s.connect()
    a.identity_id = "alice"
    return s, a, client


def test_paris_two_branches_success_are_unknown_not_attested_complete():
    a = account()
    s = paris([a])
    try:
        snap = read_loan_inventory(s, a)
        assert snap.status == "success" and len(snap.loans) == 2
        assert {(b.upstream, b.medium, b.completeness) for b in snap.branches} == {
            ("dilicom", "digital", "unknown"),
            ("syracuse", "physical", "unknown"),
        }
        assert not snap.branch_claims_complete("dilicom", "digital")
        assert not snap.branch_claims_complete("syracuse", "physical")
        assert s.client.calls == [("DILICOM", ""), ("SYRACUSE", "")]
        assert all(row.identity_id == a.identity_id for row in snap.loans)
        assert all(row.source_account_id == "primary" for row in snap.loans)
    finally:
        s.close()


@pytest.mark.parametrize("failed", ["DILICOM", "SYRACUSE"])
def test_paris_broken_one_branch_keeps_other_positive_and_marks_partial(failed):
    a = account()
    s = paris([a])
    s.client.fail.add(failed)
    try:
        snap = read_loan_inventory(s, a)
        assert snap.status == "partial" and snap.reason_code == "branch_unavailable"
        assert len(snap.loans) == 1
        assert snap.loans[0].provider != failed
        branch = next(b for b in snap.branches if b.upstream == failed.casefold())
        assert branch.completeness == "partial"
        assert not snap.branch_claims_complete(failed.casefold(), branch.medium)
    finally:
        s.close()


def test_paris_both_branches_failed_cannot_publish_empty_success():
    a = account()
    s = paris([a])
    s.client.fail.update({"DILICOM", "SYRACUSE"})
    try:
        snap = read_loan_inventory(s, a)
        assert snap.status == "failed" and snap.loans == ()
        assert snap.reason_code == "read_failed"
    finally:
        s.close()


def test_paris_scopes_two_linked_readers_without_merging_borrower_identity():
    primary = account()
    linked = account(borrower="linked-7")
    s = paris([primary, linked])
    try:
        one = read_loan_inventory(s, primary)
        two = read_loan_inventory(s, linked)
        assert len(one.loans) == len(two.loans) == 2
        assert one.scope.account_id == ""
        assert two.scope.account_id == "linked-7"
        assert {x.source_account_id for x in one.loans} == {"primary"}
        assert {x.source_account_id for x in two.loans} == {"linked-7"}
        unknown = account(borrower="not-linked")
        assert read_loan_inventory(s, unknown).status == "failed"
    finally:
        s.close()


@pytest.mark.parametrize("html,expected_count", [
    (BNF_HTML, 1),
    ('<section class="active-loans"></section>', 0),
])
def test_bnf_real_html_adapter_never_claims_exhaustive_loans(html, expected_count):
    s, a, client = bnf(html)
    try:
        before = client.calls.count("activity")
        snap = read_loan_inventory(s, a)
        assert len(snap.loans) == expected_count
        assert snap.status == "partial"
        assert snap.reason_code == "inventory_not_exhaustive"
        assert len(snap.branches) == 1
        assert snap.branches[0].upstream == "bnf_html"
        assert snap.branches[0].completeness == "partial"
        assert not snap.branch_claims_complete("bnf_html", "digital")
        assert client.calls.count("activity") == before + 1
        assert all(row.identity_id == "alice" for row in snap.loans)
        assert not any("download" in x or "token" in x for x in client.calls)
    finally:
        s.close()


def test_bnf_wrong_credential_and_closed_session_reject_before_network():
    s, a, client = bnf()
    try:
        wrong = Account(provider_id="bnf-pret", credential_id="other",
                        identity_id="alice", unique_id="other")
        before = client.calls.count("activity")
        assert read_loan_inventory(s, wrong).status == "failed"
        assert client.calls.count("activity") == before
    finally:
        s.close()
    result = read_loan_inventory(s, a)
    assert result.status == "failed" and result.loans == ()
    assert client.calls.count("activity") == before


def test_bnf_invalid_dom_fails_without_fake_zero_coverage():
    s, a, client = bnf()
    try:
        client.html = "<html><body>Unrecognized upstream layout</body></html>"
        snapshot = read_loan_inventory(s, a)
        assert snapshot.status == "failed"
        assert snapshot.reason_code == "read_failed"
        assert not snapshot.branches and not snapshot.loans
        assert s.connected is False
    finally:
        s.close()


def test_paris_connected_reader_replaced_mid_read_cannot_publish_loan_snapshot():
    a = account()
    s = paris([a])
    original = s.client.loans
    calls = []

    def replaced(upstream, borrower):
        calls.append(upstream)
        rows = original(upstream, borrower)
        if upstream == "DILICOM":
            # A linked-account reconfiguration raced with the two reads.
            s._accounts = [account(borrower="unrelated")]
        return rows

    s.client.loans = replaced
    try:
        snap = read_loan_inventory(s, a)
        # The v1+typed Paris boundary now aborts *before* making another
        # private request when the current borrower was unregistered during
        # the first GET. It must not fetch SYRACUSE for a different account.
        assert calls == ["DILICOM"]
        assert snap.status == "failed" and snap.loans == ()
        assert snap.reason_code == "read_failed"
    finally:
        s.close()
