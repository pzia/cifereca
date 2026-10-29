"""P0 #2975: explicit source refresh must share opening's positive-loan truth.

A partial/empty read does not close any previous loan; a selected source must
not refresh or expire other borrowers' rights. Only synthetic providers/files.
"""
from __future__ import annotations

from dataclasses import replace
import json
from types import SimpleNamespace as NS

import pytest

from cifereca.core import sync_coordination
from cifereca.core.diagnostic_source_sync import public_log_fields, log_message
from cifereca.models import Account, Loan
from cifereca.providers import ProviderConfigurationError
from tests.test_sync_state_contract import _service


PRIVATE = "https://private.invalid/borrowed.epub?signature=SECRET"


def _loan(*, credential="cred", borrower="primary", item="edition", identity="default"):
    return Loan(
        id="loan-" + item, provider="DILICOM", medium="digital",
        record_id=item, title="PRIVATE title " + item,
        source_provider="paris", source_credential=credential,
        source_account_id=borrower, identity_id=identity,
        due_at="2099-12-12T00:00:00Z", ebook_url=PRIVATE,
    )


def _fixture(tmp_path, monkeypatch):
    service = _service(tmp_path)
    primary = Account(
        display_name="PRIVATE primary", unique_id="", is_primary=True,
        provider_id="paris", credential_id="cred", identity_id="default",
    )
    paired = Account(
        display_name="PRIVATE paired", unique_id="paired", is_primary=False,
        provider_id="paris", credential_id="cred", identity_id="default",
    )
    other = Account(
        display_name="PRIVATE second identity", unique_id="foreign",
        provider_id="paris", credential_id="other-cred", identity_id="alex",
    )
    service.accounts = [primary, paired, other]
    inputs = {"": [_loan()], "paired": [], "foreign": []}
    session = NS(
        provider_id="paris", credential_id="cred", connected=True,
        capabilities=NS(loans=True, personal_library=False),
        last_loan_errors=[], loans=lambda account: list(inputs[account.unique_id]),
    )
    service._sessions[service._session_key("paris", "cred")] = session
    monkeypatch.setattr(sync_coordination, "acquisition_lock_file",
                        lambda: tmp_path / "PRIVATE writer.lock")
    return service, primary, paired, other, inputs, session


def _right(service, account_id, item):
    return next(row for row in service.storage.entitlements(provider_id="paris")
                if row["account_id"] == account_id and row["item_id"] == item)


def test_explicit_source_persists_exact_positive_loan_without_acquisition(
    tmp_path, monkeypatch,
):
    service, primary, paired, other, inputs, session = _fixture(tmp_path, monkeypatch)
    try:
        other_loan = _loan(credential="other-cred", borrower="foreign", identity="alex",
                           item="other-edition")
        service._persist_active_loans("alex", [other_loan])
        result = service.synchronize_account_source(primary)
        assert result["loans"] == 1 and result["status"] == "partial"
        row = _right(service, "primary", "edition")
        assert row["entitlement_type"] == "loan"
        assert row["loan_id"] == "loan-edition" and row["state"] == "active"
        assert row["identity_id"] == "default" and row["credential_id"] == "cred"
        assert _right(service, "foreign", "other-edition")["state"] == "active"
        assert service.storage.acquisitions() == []
        assert service.storage.placements() == []
        assert service.storage.artifacts() == []
        # A repeated positive listing is idempotent; a missing listing is
        # NEVER automatically a negative evidence of return.
        assert service.synchronize_account_source(primary)["loans"] == 1
        assert len(service.storage.entitlements(provider_id="paris")) == 2
        inputs[""] = []
        assert service.synchronize_account_source(primary)["loans"] == 0
        assert _right(service, "primary", "edition")["state"] == "active"
        assert _right(service, "foreign", "other-edition")["state"] == "active"
        assert PRIVATE not in service.diagnostics.text("test")
    finally:
        service.close()


def test_partial_dilicom_manual_sync_persists_positive_only_and_does_not_mint_success(
    tmp_path, monkeypatch,
):
    service, primary, paired, _, inputs, session = _fixture(tmp_path, monkeypatch)
    try:
        session.last_loan_errors = [
            {"upstream": "DILICOM", "error": PRIVATE},
        ]
        previous = _loan(borrower="paired", item="paired-edition")
        service._persist_active_loans("default", [previous])
        outcome = service.synchronize_account_source(primary)
        assert outcome["status"] == "partial" and outcome["loans"] == 1
        assert _right(service, "primary", "edition")["state"] == "active"
        assert _right(service, "paired", "paired-edition")["state"] == "active"
        # The provider-side secret never becomes a Core event or action.
        events = [event for event in service.diagnostics.snapshot()
                  if event.get("verified_source_sync")]
        assert [event["detail"]["status"] for event in events] == [
            "running", "partial",
        ]
        terminal = events[-1]["detail"]
        assert terminal["code"] == "source_sync_partial"
        assert public_log_fields(log_message(terminal)) == terminal
        assert PRIVATE not in json.dumps(events)
        assert PRIVATE not in json.dumps(service.storage.action_event_counts(
            identity_id="default", since=0,
        ))
        # Even a completely empty response from this partial producer
        # cannot realize either pre-existing loan.
        inputs[""] = []
        again = service.synchronize_account_source(primary)
        assert again["status"] == "partial" and again["loans"] == 0
        assert _right(service, "primary", "edition")["state"] == "active"
        assert _right(service, "paired", "paired-edition")["state"] == "active"
    finally:
        service.close()


@pytest.mark.parametrize("attack", ["foreign_loan", "rebind", "disable", "epoch"])
def test_manual_source_rejects_mixed_or_stale_account_before_rights_write(
    tmp_path, monkeypatch, attack,
):
    service, primary, paired, _, inputs, session = _fixture(tmp_path, monkeypatch)
    try:
        if attack == "foreign_loan":
            inputs[""] = [_loan(borrower="paired", item="foreign")]
        else:
            original = session.loans

            def change_during_read(account):
                rows = original(account)
                if attack == "rebind":
                    service.accounts = [paired]
                elif attack == "disable":
                    service.storage.metadata_set(
                        "sync.source.enabled." + service.sync_source_id(primary), "0",
                    )
                elif attack == "epoch":
                    service._source_context_epoch += 1
                return rows

            session.loans = change_during_read
        with pytest.raises(ProviderConfigurationError):
            service.synchronize_account_source(primary)
        assert not service.storage.entitlements(provider_id="paris")
        assert not service.storage.acquisitions()
        assert not service.storage.placements()
    finally:
        service.close()


def test_partial_empty_source_never_expires_a_different_account(
    tmp_path, monkeypatch,
):
    service, primary, paired, _, inputs, session = _fixture(tmp_path, monkeypatch)
    try:
        expired = replace(_loan(borrower="paired", item="historical"),
                          due_at="2000-01-01T00:00:00Z")
        service._persist_active_loans("default", [expired])
        # Earlier persisted expiry transitions may occur as part of the
        # historical opening flow, but manual source refresh does not touch it.
        with service.storage._lock, service.storage._connect() as db:
            db.execute("UPDATE entitlements SET state='active' WHERE loan_id=?",
                       ("loan-historical",))
        inputs[""] = []
        assert service.synchronize_account_source(primary)["loans"] == 0
        assert _right(service, "paired", "historical")["state"] == "active"
    finally:
        service.close()
