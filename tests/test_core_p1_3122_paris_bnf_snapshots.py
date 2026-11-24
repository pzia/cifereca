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
        snap = read_loan_inventory(s, a, descriptor=s.descriptor)
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
        snap = read_loan_inventory(s, a, descriptor=s.descriptor)
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
        snap = read_loan_inventory(s, a, descriptor=s.descriptor)
        assert snap.status == "failed" and snap.loans == ()
        assert snap.reason_code == "read_failed"
    finally:
        s.close()


def test_paris_scopes_two_linked_readers_without_merging_borrower_identity():
    primary = account()
    linked = account(borrower="linked-7")
    s = paris([primary, linked])
    try:
        one = read_loan_inventory(s, primary, descriptor=s.descriptor)
        two = read_loan_inventory(s, linked, descriptor=s.descriptor)
        assert len(one.loans) == len(two.loans) == 2
        assert one.scope.account_id == ""
        assert two.scope.account_id == "linked-7"
        assert {x.source_account_id for x in one.loans} == {"primary"}
        assert {x.source_account_id for x in two.loans} == {"linked-7"}
        unknown = account(borrower="not-linked")
        assert read_loan_inventory(s, unknown, descriptor=s.descriptor).status == "failed"
    finally:
        s.close()


@pytest.mark.parametrize("html,expected_count", [
    (BNF_HTML, 1),
    ('<section class="active-loans"></section>', 0),
])
def test_bnf_validated_html_claims_scoped_coverage_including_empty(html, expected_count):
    s, a, client = bnf(html)
    try:
        before = client.calls.count("activity")
        snap = read_loan_inventory(s, a, descriptor=s.descriptor)
        assert len(snap.loans) == expected_count
        complete = True
        assert snap.status == "success"
        assert snap.reason_code is None
        assert len(snap.branches) == 1
        assert snap.branches[0].upstream == "bnf_html"
        assert snap.branches[0].completeness == ("complete" if complete else "partial")
        assert snap.branches[0].pagination_complete is (True if complete else None)
        assert snap.branch_claims_complete("bnf_html", "digital") is complete
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
        snap = read_loan_inventory(s, a, descriptor=s.descriptor)
        # The v1+typed Paris boundary now aborts *before* making another
        # private request when the current borrower was unregistered during
        # the first GET. It must not fetch SYRACUSE for a different account.
        assert calls == ["DILICOM"]
        assert snap.status == "failed" and snap.loans == ()
        assert snap.reason_code == "read_failed"
    finally:
        s.close()


@pytest.mark.parametrize("session_class", [ParisProviderSession, BnfProviderSession])
def test_shared_adapter_on_real_provider_classes(session_class):
    from cifereca.plugin_api import LegacyRightsResourceAdapter, ProviderSession
    assert issubclass(session_class, LegacyRightsResourceAdapter)
    assert session_class.downloadable_resource_snapshot is LegacyRightsResourceAdapter.downloadable_resource_snapshot
    assert session_class.downloadable_selected_offer_snapshot is LegacyRightsResourceAdapter.downloadable_selected_offer_snapshot
    assert session_class._downloadable_scope is LegacyRightsResourceAdapter._downloadable_scope
    assert not hasattr(ProviderSession, "downloadable_resource_snapshot")


@pytest.mark.parametrize("provider", ["paris", "bnf"])
def test_real_provider_candidate_wrapper_preserves_revocation(provider, tmp_path):
    from unittest.mock import Mock
    from cifereca.plugin_api import ProviderCapabilityError
    if provider == "paris":
        a = account()
        s = paris([a])
        branch = "dilicom"
    else:
        s, a, _ = bnf()
        branch = "bnf_html"
    staged = tmp_path / "candidate.acsm"
    def transfer(*args, **kwargs):
        staged.write_bytes(b"synthetic")
        a.identity_id = ""
        return staged, "acsm"
    s.rights_selected_delivery_candidate = Mock(side_effect=transfer)
    try:
        with pytest.raises(ProviderCapabilityError, match="selected_delivery_scope_changed"):
            s._downloadable_selected_delivery_candidate(
                a, edition_id="edition", resource_id="loan", kind="loan",
                branch=branch, selected_offer_id="offer", target_dir=tmp_path,
            )
        assert not staged.exists()
        s.rights_selected_delivery_candidate.assert_called_once()
    finally:
        s.close()


def test_bnf_synthetic_empty_inventory_is_not_negative_rights_evidence():
    """#4480: manual inventory completeness is not rights revocation."""
    s, a, client = bnf()
    s.identity_broker = NS(accounts=lambda identity: [NS(
        provider_id=a.provider_id, credential_id=a.credential_id,
        account_id=a.unique_id, identity_id=identity, scope_version=2,
    )] if identity == a.identity_id else [])
    try:
        first = read_loan_inventory(s, a, descriptor=s.descriptor)
        assert len(first.loans) == 1
        client.html = '<section class="active-loans"></section>'
        empty = read_loan_inventory(s, a, descriptor=s.descriptor)
        assert empty.status == "success" and not empty.loans
        assert empty.branch_claims_complete("bnf_html", "digital")
        rights = s.rights_observation_snapshot(a)
        resources = s.downloadable_resource_snapshot(a)
        assert not rights.rights and rights.coverage == "partial"
        assert not resources.resources and resources.coverage == "partial"
        assert resources.scope == rights.scope
        assert resources.branch == rights.branch == "bnf_html"
        assert not resources.authorizes_negative_reconciliation
        assert not resources.authorizes_acquisition
        client.html = BNF_HTML
        again = read_loan_inventory(s, a, descriptor=s.descriptor)
        assert again.status == "success" and len(again.loans) == 1
        assert again.scope == first.scope == empty.scope
        assert again.loans[0].id == first.loans[0].id
        assert set(client.calls) <= {"login", "activity"}
    finally:
        s.close()


@pytest.mark.parametrize("count", [1, 3])
def test_bnf_zero_remaining_quota_preserves_active_inventory(count):
    """#4480/#4063: remaining borrow capacity is not active-loan count."""
    cards = "".join(
        '<div class="js-loan">'
        f'<a href="/resources/book-{i}">Synthetic novel {i}</a>'
        f'<a href="/loans/loan-{i}">Private token action</a>'
        '</div>' for i in range(count)
    )
    html = ('<p>Vous avez encore droit à <strong>0</strong> prêt</p>'
            '<section class="active-loans">' + cards + '</section>')
    s, a, client = bnf(html)
    try:
        snapshot = read_loan_inventory(s, a, descriptor=s.descriptor)
        assert snapshot.status == "success"
        assert snapshot.branch_claims_complete("bnf_html", "digital")
        assert len(snapshot.loans) == count
        assert len({row.id for row in snapshot.loans}) == count
        assert all(row.identity_id == a.identity_id for row in snapshot.loans)
        assert set(client.calls) <= {"login", "activity"}
    finally:
        s.close()


def test_bnf_real_session_core_cache_nonempty_empty_reobserved(tmp_path):
    """#4480: exercise the persisted direct inventory, not rights revocation."""
    from cifereca.services import _loan_cache_key
    from tests.test_sync_state_contract import _service

    service = _service(tmp_path)
    s, a, client = bnf()
    try:
        service.accounts = [a]
        service._sessions[service._session_key(a.provider_id, a.credential_id)] = s
        key = _loan_cache_key(a)
        first = service._loans_for_account(a, force=True)
        assert len(first) == 1
        assert len(service.storage.cache_get("account", key)) == 1
        client.html = '<section class="active-loans"></section>'
        assert service._loans_for_account(a, force=True) == []
        assert service.storage.cache_get("account", key) == []
        client.html = BNF_HTML
        again = service._loans_for_account(a, force=True)
        assert [row.id for row in again] == [row.id for row in first]
        assert len(service.storage.cache_get("account", key)) == 1
        assert service.storage.entitlements(provider_id="bnf-pret") == []
        assert service.storage.acquisitions() == []
        assert client.calls.count("activity") == 3
    finally:
        service.close()
        s.close()


@pytest.mark.parametrize("count", [0, 1, 3])
def test_paris_real_inventory_cardinality_preserves_branch_coverage(count):
    a = account()
    s = paris([a])
    original = s.client.loans

    def rows(upstream, borrower):
        template = original(upstream, borrower)[0]
        from dataclasses import replace
        return [replace(template, id=f"{template.id}-{i}",
                        record_id=f"{template.record_id}-{i}")
                for i in range(count)]

    s.client.loans = rows
    try:
        snap = read_loan_inventory(s, a, descriptor=s.descriptor)
        assert snap.status == "success"
        assert len(snap.loans) == 2 * count
        assert len({row.id for row in snap.loans}) == 2 * count
        assert all(row.identity_id == a.identity_id for row in snap.loans)
        assert snap.branch_claims_complete("dilicom", "digital") is (count == 0)
        assert snap.branch_claims_complete("syracuse", "physical") is (count == 0)
        assert s.client.calls == [("DILICOM", ""), ("SYRACUSE", "")]
    finally:
        s.close()


@pytest.mark.parametrize("entrypoint", ["loans", "loan_inventory_snapshot"])
def test_bnf_cancelled_activity_never_returns_inventory(entrypoint):
    from cifereca.plugin_api import Cancelled

    s, a, client = bnf()
    original = client.activity
    try:
        first = read_loan_inventory(s, a, descriptor=s.descriptor)
        assert first.status == "success" and len(first.loans) == 1
        before = client.calls.count("activity")

        def cancelled():
            client.calls.append("activity")
            raise Cancelled("synthetic cancellation")

        client.activity = cancelled
        with pytest.raises(Cancelled, match="synthetic cancellation"):
            getattr(s, entrypoint)(a)
        assert client.calls.count("activity") == before + 1
        assert s._last_loan_inventory_evidence is None
        client.activity = original
        again = read_loan_inventory(s, a, descriptor=s.descriptor)
        assert again.status == "success"
        assert [row.id for row in again.loans] == [row.id for row in first.loans]
    finally:
        s.close()
