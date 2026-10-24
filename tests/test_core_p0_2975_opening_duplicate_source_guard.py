"""#2975: contradictory duplicate supplier loans are never first-row-wins.

Two independent Paris accounts are synthetic; no BnF-Prêt plugin, ADEPT,
file transfer, provider request, or negative loan realization is invoked.
"""
from __future__ import annotations

from dataclasses import replace
import json

import pytest

from cifereca.models import Account
from tests.test_core_p0_1346_opening_source_isolation import _state
from tests.test_core_p0_2975_loan_absence_scope import _loan
from tests.test_sync_state_contract import _service


def _account(credential, borrower):
    return Account(
        display_name="PRIVATE reader", unique_id=borrower,
        provider_id="paris", credential_id=credential, identity_id="default",
    )


@pytest.mark.parametrize("change", ["edition", "due", "medium", "provider"])
def test_conflicting_loan_on_second_page_rejects_entire_account_but_not_sibling(
    tmp_path, monkeypatch, change,
):
    wrong = _account("cred-wrong", "reader-wrong")
    healthy = _account("cred-good", "reader-good")
    first = _loan("repeated", credential="cred-wrong", account="reader-wrong")
    collision = {
        "edition": replace(first, record_id="PRIVATE_DIFFERENT_EDITION"),
        "due": replace(first, due_at="2081-01-01T00:00:00Z"),
        "medium": replace(first, medium="physical"),
        "provider": replace(first, provider="SYRACUSE"),
    }[change]
    valid = _loan("healthy", credential="cred-good", account="reader-good")
    rows = {"cred-wrong": [first, collision], "cred-good": [valid]}
    service = _service(tmp_path)
    try:
        service.accounts = [wrong, healthy]
        monkeypatch.setattr(service, "_loans_for_account",
                            lambda a, *, force=False: list(rows[a.credential_id]))
        monkeypatch.setattr(service, "_acquire_active_loan_automatically",
                            lambda loan: {"status": "unchanged"})
        report = service.opening_sync()
        identity = next(row for row in report["identities"]
                        if row["identity_id"] == "default")
        assert identity["status"] == "partial"
        assert _state(service, wrong)["status"] == "failed"
        assert _state(service, healthy)["status"] == "success"
        assert identity["realized"] == 0
        assert {r["loan_id"] for r in service.storage.entitlements()} == {valid.id}
        assert [x.id for x in service.last_loans] == [valid.id]
        assert not service.storage.acquisitions()
        assert not service.storage.placements()
        assert "PRIVATE_DIFFERENT_EDITION" not in json.dumps(report)
    finally:
        service.close()


def test_identical_page_duplicates_are_one_positive_loan(tmp_path, monkeypatch):
    account = _account("cred", "reader")
    loan = _loan("same", credential="cred", account="reader")
    service = _service(tmp_path)
    try:
        service.accounts = [account]
        monkeypatch.setattr(service, "_loans_for_account",
                            lambda a, *, force=False: [loan, replace(loan)])
        monkeypatch.setattr(service, "_acquire_active_loan_automatically",
                            lambda l: {"status": "unchanged"})
        result = service.opening_sync()
        assert {r["loan_id"] for r in service.storage.entitlements()} == {loan.id}
        assert len(service.last_loans) == 1
        assert _state(service, account)["status"] == "success"
    finally:
        service.close()


def test_duplicate_ids_from_different_borrowers_do_not_conflict(tmp_path, monkeypatch):
    alice, bob = _account("cred", "alice"), _account("cred", "bob")
    loan = _loan("shared", credential="cred", account="alice")
    other = replace(loan, source_account_id="bob", record_id="book-different")
    service = _service(tmp_path)
    try:
        service.accounts = [alice, bob]
        monkeypatch.setattr(service, "_loans_for_account",
                            lambda a, *, force=False: [loan] if a is alice else [other])
        monkeypatch.setattr(service, "_acquire_active_loan_automatically",
                            lambda l: {"status": "unchanged"})
        service.opening_sync()
        rights = service.storage.entitlements()
        assert len(rights) == 2
        assert {r["account_id"] for r in rights} == {"alice", "bob"}
        assert _state(service, alice)["status"] == "success"
        assert _state(service, bob)["status"] == "success"
    finally:
        service.close()
