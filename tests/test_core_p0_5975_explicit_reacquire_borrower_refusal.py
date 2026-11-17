"""P0 #5975: explicit reacquisition cannot interpret a falsey refusal as a grant.

The historic Paris borrower resolver is an external/read-only callback.
Only a literal None refusal means approval; untrusted refusal values cannot
trigger a provider GET, a ledger write or an unbounded public error code.
"""
from __future__ import annotations

from contextlib import nullcontext

import pytest

import cifereca.services as services_module
from cifereca.models import Loan
from tests.test_sync_state_contract import _service


class _HostileRefusal:
    def __bool__(self):
        raise AssertionError("refusal truthiness must not be evaluated")

    def __str__(self):
        raise AssertionError("private refusal must not be formatted")


@pytest.mark.parametrize("provider", ["paris", "biblioaccess.la-rochelle"])
@pytest.mark.parametrize(("refusal", "public_code"), [
    ("", "adept_borrower_ambiguous"),
    (False, "adept_borrower_ambiguous"),
    (0, "adept_borrower_ambiguous"),
    ([], "adept_borrower_ambiguous"),
    (_HostileRefusal(), "adept_borrower_ambiguous"),
    ("private provider error", "adept_borrower_ambiguous"),
    ("adept_borrower_binding_required", "adept_borrower_binding_required"),
])
def test_explicit_reacquisition_refuses_malformed_or_falsey_borrower_grant(
    tmp_path, monkeypatch, refusal, public_code, provider,
):
    service = _service(tmp_path)
    try:
        service.storage.provider_connection_put({
            "provider_id": provider, "credential_id": "credential-a",
            "identity_id": "default",
        })
        loan = Loan(
            id="loan-1", record_id="edition-1", provider="PNB",
            medium="digital", title="Test admission",
            source_provider=provider, source_credential="credential-a",
            source_account_id="primary", identity_id="default",
        )

        def no_downstream(*_args, **_kwargs):
            pytest.fail("borrower refusal crossed a persistence/provider boundary")

        with monkeypatch.context() as patch:
            patch.setattr(services_module, "InterProcessLock", lambda *_a: nullcontext())
            patch.setattr(
                services_module, "acquisition_lock_file",
                lambda: tmp_path / "reacquire.lock",
            )
            patch.setattr(service, "can_download_loan", lambda _loan: True)
            patch.setattr(
                service, "_paris_adept_identity_for_loan",
                lambda *_a, **_kw: ("default", refusal),
            )
            patch.setattr(service, "_cross_identity_loan_conflict", no_downstream)
            patch.setattr(service, "_persist_active_loans", no_downstream)
            patch.setattr(service, "_acquire_active_loan_automatically", no_downstream)
            patch.setattr(service.storage, "acquisition_put", no_downstream)
            patch.setattr(service.storage, "entitlement_put", no_downstream)

            receipt = service._reacquire_digital_loan_impl(loan)

        assert receipt == {
            "status": "attention_required",
            "loan_id": loan.id,
            "code": public_code,
            "retry": "explicit_only",
        }
        assert service.storage.acquisitions(provider_id=provider) == []
        assert service.storage.entitlements(provider_id=provider) == []
    finally:
        service.close()


@pytest.mark.parametrize("provider", ["paris", "biblioaccess.la-rochelle"])
@pytest.mark.parametrize("borrower", [None, "", False, 0, object()])
def test_explicit_reacquire_requires_positive_native_borrower_before_rights(
    tmp_path, monkeypatch, provider, borrower,
):
    """(None, None) is not a source-scoped grant for any lender."""
    service = _service(tmp_path)
    try:
        service.storage.provider_connection_put({
            "provider_id": provider, "credential_id": "credential-a",
            "identity_id": "default",
        })
        loan = Loan(
            id="loan-1", record_id="edition-1", provider="PNB",
            medium="digital", title="Test borrower",
            source_provider=provider, source_credential="credential-a",
            source_account_id="primary", identity_id="default",
        )

        def forbidden(*_args, **_kwargs):
            pytest.fail("unproven borrower reached rights, persistence or GET")

        with monkeypatch.context() as patch:
            patch.setattr(services_module, "InterProcessLock", lambda *_a: nullcontext())
            patch.setattr(service, "can_download_loan", lambda _loan: True)
            patch.setattr(
                service, "_paris_adept_identity_for_loan",
                lambda *_a, **_kw: (borrower, None),
            )
            patch.setattr(service.storage, "acquisitions", forbidden)
            patch.setattr(service.storage, "entitlements", forbidden)
            patch.setattr(service, "_persist_active_loans", forbidden)
            patch.setattr(service, "_acquire_active_loan_automatically", forbidden)
            assert service._reacquire_digital_loan_impl(loan) == {
                "status": "attention_required", "loan_id": "loan-1",
                "code": "adept_borrower_ambiguous", "retry": "explicit_only",
            }
    finally:
        service.close()


@pytest.mark.parametrize("provider", ["paris", "biblioaccess.la-rochelle"])
def test_explicit_reacquire_borrower_exception_refuses_before_rights(
    tmp_path, monkeypatch, provider,
):
    service = _service(tmp_path)
    try:
        service.storage.provider_connection_put({
            "provider_id": provider, "credential_id": "credential-a",
            "identity_id": "default",
        })
        loan = Loan(
            id="loan-1", record_id="edition-1", provider="PNB",
            medium="digital", title="Test borrower exception",
            source_provider=provider, source_credential="credential-a",
            source_account_id="primary", identity_id="default",
        )

        def forbidden(*_args, **_kwargs):
            pytest.fail("resolver failure reached rights, persistence or GET")

        def broken_grant(*_args, **_kwargs):
            raise RuntimeError("private Adobe credential")

        with monkeypatch.context() as patch:
            patch.setattr(services_module, "InterProcessLock", lambda *_a: nullcontext())
            patch.setattr(service, "can_download_loan", lambda _loan: True)
            patch.setattr(service, "_paris_adept_identity_for_loan", broken_grant)
            patch.setattr(service.storage, "acquisitions", forbidden)
            patch.setattr(service.storage, "entitlements", forbidden)
            patch.setattr(service, "_persist_active_loans", forbidden)
            patch.setattr(service, "_acquire_active_loan_automatically", forbidden)
            result = service._reacquire_digital_loan_impl(loan)
            assert result == {
                "status": "attention_required", "loan_id": "loan-1",
                "code": "adept_borrower_ambiguous", "retry": "explicit_only",
            }
            assert "private" not in repr(result)
    finally:
        service.close()
