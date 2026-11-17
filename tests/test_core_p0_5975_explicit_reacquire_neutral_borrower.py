"""#5975: explicit reacquisition must validate a linked non-Paris ADEPT borrower.

Uses real SQLite grants and synthetic accounts only. The test intercepts before
any materialization, GET/POST or ledger mutation.
"""
from __future__ import annotations

from contextlib import nullcontext

import pytest

import cifereca.services as services_module
from tests.test_core_p0_5975_poststaging_adept_all_sources import (
    _foreign_linked_borrower,
)
from tests.test_paris_borrower_adept_binding import service_with_linked_borrower


@pytest.mark.parametrize("provider", [
    "biblioaccess.la-rochelle", "synthetic-library",
])
def test_linked_borrower_requires_own_source_v16_grant_before_explicit_staging(
    tmp_path, monkeypatch, provider,
):
    service, paris_loan = service_with_linked_borrower(tmp_path)
    try:
        loan = _foreign_linked_borrower(service, paris_loan, provider=provider)
        monkeypatch.setattr(
            services_module, "InterProcessLock", lambda *_a: nullcontext(),
        )
        monkeypatch.setattr(service, "can_download_loan", lambda _: True)
        # A conflict verdict proves the borrower check completed without
        # entering persistent staging, and does not grant retrieval rights.
        conflict_calls = []
        monkeypatch.setattr(
            service, "_cross_identity_loan_conflict",
            lambda *_a, **_kw: (conflict_calls.append("read") or True),
        )
        def forbidden(*_args, **_kwargs):
            pytest.fail("explicit preflight must not stage or contact provider")
        monkeypatch.setattr(service, "_persist_active_loans", forbidden)
        monkeypatch.setattr(
            service, "_acquire_active_loan_automatically", forbidden,
        )
        loan_provider = loan.source_provider
        before = service._reacquire_digital_loan_impl(loan)
        assert before == {
            "status": "attention_required", "loan_id": loan.id,
            "code": "adept_borrower_binding_required", "retry": "explicit_only",
        }
        assert conflict_calls == []

        coords = dict(
            provider_id=provider, credential_id=loan.source_credential,
            account_id=loan.source_account_id,
        )
        service.storage.adept_borrower_grant_put(
            **coords, connection_identity_id="alice",
            borrower_identity_id="bob", profile_id="bob-profile",
            confirmed=True,
        )
        admitted = service._reacquire_digital_loan_impl(loan)
        assert admitted == {
            "status": "attention_required", "loan_id": loan.id,
            "code": "adept_loan_identity_conflict", "retry": "explicit_only",
        }
        assert conflict_calls == ["read"]

        service.storage.adept_borrower_grant_delete(**coords)
        after = service._reacquire_digital_loan_impl(loan)
        assert after == before and conflict_calls == ["read"]
        assert service.storage.acquisitions(provider_id=loan_provider) == []
        assert service.storage.entitlements(provider_id=loan_provider) == []
    finally:
        service.close()
