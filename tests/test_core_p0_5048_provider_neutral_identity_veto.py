"""P0 #5048: cross-identity automatic loan veto belongs to Core, not Paris.

Tests use disposable SQLite and two synthetic providers. No network, ADEPT,
download, entitlement mutation, or source-consent fallback is permitted.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from cifereca.core.loan_acquisition_admission import resolve
from cifereca.models import Account
from tests.test_automatic_loan_admission import _setup, _forbid_work, LOGGER


def _source(tmp_path, provider="synthetic-a"):
    service, paris_loan = _setup(tmp_path, bound=False)
    service.storage.provider_connection_put({
        "identity_id": "default", "provider_id": provider,
        "credential_id": "source-a",
    })
    loan = replace(paris_loan, provider=provider, source_provider=provider)
    service.accounts = [Account(
        provider_id=provider, credential_id="source-a", identity_id="default",
        unique_id="", is_primary=True,
    )]
    return service, loan


def _foreign_acquisition(service, loan, *, provider=None, identity="alex"):
    service.storage.acquisition_put({
        "acquisition_id": f"prior-{provider or loan.source_provider}-{identity}",
        "provider_id": provider or loan.source_provider,
        "credential_id": "source-b", "account_id": "borrower-b",
        "identity_id": identity, "item_id": loan.record_id,
        "status": "complete", "strategy": "loan_auto",
        "metadata": {"provider_loan_id": loan.id},
    })


def _foreign_right(service, loan, *, provider=None, identity="alex"):
    service.storage.entitlement_put({
        "entitlement_id": f"right-{provider or loan.source_provider}-{identity}",
        "identity_id": identity, "provider_id": provider or loan.source_provider,
        "credential_id": "source-b", "account_id": "borrower-b",
        "item_id": loan.record_id, "entitlement_type": "loan",
        "state": "active", "loan_id": loan.id,
        "metadata": {"title": "synthetic"},
    })


@pytest.mark.parametrize("provider", ["synthetic-a", "synthetic-b"])
@pytest.mark.parametrize("ledger", ["acquisition", "right"])
def test_other_identity_is_refused_for_any_provider_without_provider_work(
    tmp_path, monkeypatch, provider, ledger,
):
    service, loan = _source(tmp_path, provider)
    try:
        if ledger == "acquisition":
            _foreign_acquisition(service, loan)
        else:
            _foreign_right(service, loan)
        before_rights = service.storage.entitlements(provider_id=provider)
        before_acquisitions = service.storage.acquisitions(provider_id=provider)
        _forbid_work(service, monkeypatch)
        result = resolve(service, loan, _TRACE_LOG=LOGGER)
        assert result.identity is None and result.entitlement_id is None
        assert result.refusal == {
            "status": "attention_required", "loan_id": loan.id,
            "code": "adept_loan_identity_conflict",
            "reason": "cross_identity_loan", "retry": "explicit_only",
        }
        assert service.storage.entitlements(provider_id=provider) == before_rights
        assert service.storage.acquisitions(provider_id=provider) == before_acquisitions
        assert service.current_identity.identity_id == "alex"
    finally:
        service.close()


@pytest.mark.parametrize("ledger", ["acquisition", "right"])
def test_identical_upstream_loan_in_unrelated_provider_is_not_a_conflict(
    tmp_path, monkeypatch, ledger,
):
    service, loan = _source(tmp_path, "synthetic-a")
    try:
        if ledger == "acquisition":
            _foreign_acquisition(service, loan, provider="synthetic-b")
        else:
            _foreign_right(service, loan, provider="synthetic-b")
        _forbid_work(service, monkeypatch)
        result = resolve(service, loan, _TRACE_LOG=LOGGER)
        assert result.refusal is None
        assert result.identity.identity_id == "default"
        assert result.entitlement_id is None
    finally:
        service.close()


@pytest.mark.parametrize("ledger", ["acquisition", "right"])
def test_same_identity_history_is_not_a_cross_identity_veto(
    tmp_path, monkeypatch, ledger,
):
    service, loan = _source(tmp_path, "synthetic-b")
    try:
        if ledger == "acquisition":
            _foreign_acquisition(service, loan, identity="default")
        else:
            _foreign_right(service, loan, identity="default")
        _forbid_work(service, monkeypatch)
        result = resolve(service, loan, _TRACE_LOG=LOGGER)
        assert result.refusal is None
        assert result.identity.identity_id == "default"
        # The existing right is owned by a different credential/borrower.
        assert result.entitlement_id is None
    finally:
        service.close()


def test_missing_loan_key_refuses_without_using_ui_identity(tmp_path, monkeypatch):
    service, loan = _source(tmp_path)
    try:
        loan = replace(loan, id="")
        _forbid_work(service, monkeypatch)
        result = resolve(service, loan, _TRACE_LOG=LOGGER)
        assert result.refusal is not None
        assert result.refusal["code"] == "adept_loan_identity_conflict"
        assert result.identity is None
    finally:
        service.close()
