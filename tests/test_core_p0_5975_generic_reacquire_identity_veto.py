"""P0 #5975: explicit reacquisition must veto foreign loan ownership for every provider.

All tests use synthetic sources and disposable SQLite. A conflict or stale
callback cannot issue a provider GET, create a right or write an acquisition.
The cross-identity ledger veto is negative evidence, never permission to
acquire the loan or open an ADEPT license.
"""
from __future__ import annotations

from contextlib import nullcontext

import pytest

import cifereca.services as services_module
from cifereca.models import Loan
from tests.test_sync_state_contract import _service


def _setup(tmp_path, monkeypatch, *, provider="synthetic-library"):
    service = _service(tmp_path)
    service.storage.provider_connection_put({
        "identity_id": "default", "provider_id": provider,
        "credential_id": "card-1",
    })
    loan = Loan(
        id="loan-shared", record_id="edition-1", provider="PNB",
        medium="digital", title="Synthetic loan",
        source_provider=provider, source_credential="card-1",
        source_account_id="reader-1", identity_id="default",
    )
    monkeypatch.setattr(services_module, "InterProcessLock",
                        lambda *_a: nullcontext())
    monkeypatch.setattr(services_module, "acquisition_lock_file",
                        lambda: tmp_path / "reacquire.lock")
    monkeypatch.setattr(service, "can_download_loan", lambda _loan: True)
    return service, loan


def _foreign_loan(service, loan, *, ledger, provider=None, owner="alex",
                  loan_id=None):
    source = provider or loan.source_provider
    upstream = loan_id or loan.id
    if ledger == "acquisition":
        service.storage.acquisition_put({
            "acquisition_id": "foreign-1",
            "provider_id": source, "credential_id": "card-foreign",
            "account_id": "reader-foreign", "identity_id": owner,
            "item_id": loan.record_id, "status": "complete",
            "strategy": "loan_auto", "executor": "kobo-adept",
            "metadata": {"provider_loan_id": upstream},
        })
    else:
        service.storage.entitlement_put({
            "entitlement_id": "right-foreign-1",
            "identity_id": owner, "provider_id": source,
            "credential_id": "card-foreign", "account_id": "reader-foreign",
            "item_id": loan.record_id, "entitlement_type": "loan",
            "state": "active", "loan_id": upstream, "metadata": {},
        })


def _forbid_side_effects(service, monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("cross-identity reacquisition escaped to provider or ledger")
    monkeypatch.setattr(service, "_persist_active_loans", forbidden)
    monkeypatch.setattr(service, "_acquire_active_loan_automatically", forbidden)
    monkeypatch.setattr(service.storage, "acquisition_put", forbidden)
    monkeypatch.setattr(service.storage, "entitlement_put", forbidden)


@pytest.mark.parametrize("provider", ["synthetic-library", "another-library"])
@pytest.mark.parametrize("ledger", ["acquisition", "right"])
def test_foreign_owner_blocks_explicit_reacquisition_for_any_provider(
    tmp_path, monkeypatch, provider, ledger,
):
    service, loan = _setup(tmp_path, monkeypatch, provider=provider)
    try:
        _foreign_loan(service, loan, ledger=ledger)
        before_acquisitions = service.storage.acquisitions()
        before_entitlements = service.storage.entitlements()
        _forbid_side_effects(service, monkeypatch)
        assert service._reacquire_digital_loan_impl(loan) == {
            "status": "attention_required",
            "loan_id": "loan-shared",
            "code": "adept_loan_identity_conflict",
            "retry": "explicit_only",
        }
        assert service.storage.acquisitions() == before_acquisitions
        assert service.storage.entitlements() == before_entitlements
        assert service.current_identity.identity_id == "default"
    finally:
        service.close()


@pytest.mark.parametrize("ledger", ["acquisition", "right"])
def test_other_provider_or_other_loan_cannot_veto_correct_source(
    tmp_path, monkeypatch, ledger,
):
    service, loan = _setup(tmp_path, monkeypatch)
    try:
        _foreign_loan(service, loan, ledger=ledger, provider="foreign-provider")
        _foreign_loan(service, loan, ledger=ledger, loan_id="unrelated-loan")
        persisted = []
        fetched = []
        monkeypatch.setattr(service, "_persist_active_loans",
                            lambda *a, **kw: persisted.append((a, kw)))
        monkeypatch.setattr(service, "_acquire_active_loan_automatically",
                            lambda *a, **kw: fetched.append((a, kw))
                            or {"status": "complete"})
        result = service._reacquire_digital_loan_impl(loan)
        assert result["status"] == "complete"
        assert len(persisted) == len(fetched) == 1
        assert fetched[0][1] == {"force_refresh": True}
    finally:
        service.close()


@pytest.mark.parametrize("stage", ["acquisitions", "entitlements", "veto"])
def test_nonparis_reacquisition_rejects_source_mutation_after_every_veto_hook(
    tmp_path, monkeypatch, stage,
):
    service, loan = _setup(tmp_path, monkeypatch)
    try:
        def change_source():
            loan.source_credential = "card-other"
        if stage == "acquisitions":
            original = service.storage.acquisitions
            def acquisitions(*args, **kwargs):
                rows = original(*args, **kwargs)
                change_source()
                return rows
            monkeypatch.setattr(service.storage, "acquisitions", acquisitions)
        elif stage == "entitlements":
            original = service.storage.entitlements
            def entitlements(*args, **kwargs):
                rows = original(*args, **kwargs)
                change_source()
                return rows
            monkeypatch.setattr(service.storage, "entitlements", entitlements)
        else:
            monkeypatch.setattr(service, "_cross_identity_loan_conflict",
                                lambda *_a, **_kw: change_source() or False)
        _forbid_side_effects(service, monkeypatch)
        assert service._reacquire_digital_loan_impl(loan) == {
            "status": "attention_required", "loan_id": "loan-shared",
            "code": "adept_identity_unproven",
            "reason": "source_scope_changed", "retry": "explicit_only",
        }
    finally:
        service.close()


def test_nonparis_same_owner_loan_is_not_positive_permission(
    tmp_path, monkeypatch,
):
    service, loan = _setup(tmp_path, monkeypatch)
    try:
        _foreign_loan(service, loan, ledger="acquisition", owner="default")
        _foreign_loan(service, loan, ledger="right", owner="default")
        monkeypatch.setattr(service, "_persist_active_loans", lambda *_a, **_kw: None)
        call_count = []
        def guarded_acquisition(*_a, **_kw):
            call_count.append(1)
            return {"status": "attention_required", "code": "adept_borrower_binding_required"}
        monkeypatch.setattr(service, "_acquire_active_loan_automatically",
                            guarded_acquisition)
        with pytest.raises(Exception) as caught:
            service._reacquire_digital_loan_impl(loan)
        assert getattr(caught.value, "code", None) == "adept_borrower_binding_required"
        assert len(call_count) == 1
    finally:
        service.close()
