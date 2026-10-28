"""#3628: BnF typed loan inventory is independent of the v1 loans facade.

The production provider/session and a synthetic HTML client are used; no
network, account secrets, token GET, borrowing, ADEPT or rights mutation.
"""
from dataclasses import replace

import pytest

from cifereca.plugin_api import (
    LoanInventorySnapshot, ProviderConfigurationError, read_loan_inventory,
)
from tests.test_p1_3252_snapshot_scope_mutation import HTML, fixture


def test_typed_read_ignores_poisoned_v1_facade_and_mutable_error_channel(
    monkeypatch,
):
    session, account, client = fixture()
    try:
        session.loans = lambda _: pytest.fail("typed used v1 loans")
        monkeypatch.setattr(
            type(session), "last_loan_errors",
            property(lambda _: pytest.fail("typed read v1 error channel")),
            raising=False,
        )
        snapshot = read_loan_inventory(session, replace(account))
        assert type(snapshot) is LoanInventorySnapshot
        assert snapshot.status == "partial"
        assert snapshot.reason_code == "inventory_not_exhaustive"
        assert snapshot.scope.identity_id == account.identity_id
        assert len(snapshot.loans) == 1
        assert snapshot.loans[0].source_account_id == account.unique_id
        assert snapshot.loans[0].identity_id == account.identity_id
        assert not snapshot.branch_claims_complete("bnf_html", "digital")
        assert client.calls == ["login", "activity"]
    finally:
        session.close()


@pytest.mark.parametrize(("html", "count"), [
    ('<section class="active-loans"></section>', 0),
    (HTML, 1),
    (HTML.replace("</section>", (
        '<div class="js-loan"><a href="/resources/book-other">Other</a>'
        '<a href="/loans/loan-other">Loan</a></div></section>'
    )), 2),
])
def test_typed_0_1_n_partials_and_legacy_still_usable(html, count):
    session, account, client = fixture()
    client.activity = lambda: (client.calls.append("activity") or html)
    try:
        typed = session.loan_inventory_snapshot(replace(account))
        assert len(typed.loans) == count
        assert typed.status == "partial"
        assert typed.reason_code == "inventory_not_exhaustive"
        assert all(branch.completeness != "complete"
                   for branch in typed.branches)
        # Historical extension API still returns the same positives under
        # its instance-only rules, without turning the list into an absence.
        legacy = session.loans(account)
        assert len(legacy) == count
        assert client.calls == ["login", "activity", "activity"]
    finally:
        session.close()


def test_typed_invalid_account_refuses_before_html_get():
    session, account, client = fixture()
    try:
        with pytest.raises(ProviderConfigurationError):
            session.loan_inventory_snapshot(
                replace(account, credential_id="other-credential"),
            )
        assert client.calls == ["login"]
    finally:
        session.close()
