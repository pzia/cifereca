"""P0 #5975: explicit recovery must reattest the enabled SQLite source owner.

Real disposable SQLite and synthetic provider/loan callbacks only. No provider
GET, ADEPT runtime or acquisition ledger writes are permitted on a stale owner.
"""
from __future__ import annotations

import pytest

from cifereca.models import Account, Loan
from tests.test_core_p0_5975_explicit_reacquire_native_scope import (
    _setup, _no_staging,
)


def _loan(provider: str) -> Loan:
    return Loan(
        id="loan-a", record_id="edition-a", provider="PNB",
        medium="digital", title="Synthetic",
        source_provider=provider, source_credential="credential-a",
        source_account_id="primary", identity_id="default",
    )


def _expected():
    return {
        "status": "attention_required", "loan_id": "loan-a",
        "code": "adept_identity_unproven",
        "reason": "source_scope_changed", "retry": "explicit_only",
    }


@pytest.mark.parametrize("provider", ["paris", "synthetic-pnb"])
@pytest.mark.parametrize("state", ["disabled", "rebound", "deleted"])
def test_explicit_recovery_refuses_stale_owner_before_grant_or_staging(
    tmp_path, monkeypatch, provider, state,
):
    service = _setup(tmp_path, monkeypatch)
    try:
        service.storage.provider_connection_put({
            "provider_id": provider, "credential_id": "credential-a",
            "identity_id": "default", "enabled": True,
        })
        loan = _loan(provider)
        monkeypatch.setattr(service, "can_download_loan", lambda _: True)
        _no_staging(service, monkeypatch)
        if state == "disabled":
            service.storage.provider_connection_put({
                "provider_id": provider, "credential_id": "credential-a",
                "identity_id": "default", "enabled": False,
            })
        elif state == "rebound":
            # The source owner is captured at admission and changes during
            # the identity callback, before any borrower grant or ledger I/O.
            previous = service.identity_by_id
            def swap_owner(identity_id):
                result = previous(identity_id)
                service.storage.provider_connection_put({
                    "provider_id": provider, "credential_id": "credential-a",
                    "identity_id": "other", "enabled": True,
                })
                return result
            monkeypatch.setattr(service, "identity_by_id", swap_owner)
        else:
            previous = service.identity_by_id
            def remove_owner(identity_id):
                result = previous(identity_id)
                with service.storage._lock, service.storage._connect() as db:
                    db.execute(
                        "DELETE FROM provider_connections "
                        "WHERE provider_id=? AND credential_id=?",
                        (provider, "credential-a"),
                    )
                return result
            monkeypatch.setattr(service, "identity_by_id", remove_owner)
        def no_grant(*_a, **_kw):
            pytest.fail("stale source reached ADEPT borrower resolver")
        monkeypatch.setattr(service, "_paris_adept_identity_for_loan", no_grant)
        assert service._reacquire_digital_loan_impl(loan) == _expected()
    finally:
        service.close()


@pytest.mark.parametrize("provider", ["paris", "synthetic-pnb"])
@pytest.mark.parametrize("callback", [
    "borrower", "conflict_inventory", "active_rights", "cross_identity",
    "prior_inventory",
])
def test_explicit_recovery_rechecks_owner_after_callback_revocation(
    tmp_path, monkeypatch, provider, callback,
):
    service = _setup(tmp_path, monkeypatch)
    try:
        service.storage.provider_connection_put({
            "provider_id": provider, "credential_id": "credential-a",
            "identity_id": "default", "enabled": True,
        })
        loan = _loan(provider)
        monkeypatch.setattr(service, "can_download_loan", lambda _: True)
        _no_staging(service, monkeypatch)
        def revoke():
            service.storage.provider_connection_put({
                "provider_id": provider, "credential_id": "credential-a",
                "identity_id": "default", "enabled": False,
            })
        if callback == "borrower":
            monkeypatch.setattr(
                service, "_paris_adept_identity_for_loan",
                lambda *_a, **_kw: (revoke() or ("default", None)),
            )
        else:
            monkeypatch.setattr(
                service, "_paris_adept_identity_for_loan",
                lambda *_a, **_kw: ("default", None),
            )
        if callback in ("conflict_inventory", "prior_inventory"):
            old = service.storage.acquisitions
            calls = {"n": 0}
            def inventory(*a, **kw):
                calls["n"] += 1
                result = old(*a, **kw)
                # Every provider traverses borrower preflight and the same
                # conflict + previous acquisition inventories.
                target = (1 if callback == "conflict_inventory" else 2)
                if calls["n"] == target:
                    revoke()
                return result
            monkeypatch.setattr(service.storage, "acquisitions", inventory)
        elif callback == "active_rights":
            old = service.storage.entitlements
            def rights(*a, **kw):
                result = old(*a, **kw)
                revoke()
                return result
            monkeypatch.setattr(service.storage, "entitlements", rights)
        elif callback == "cross_identity":
            monkeypatch.setattr(
                service, "_cross_identity_loan_conflict",
                lambda *_a, **_kw: (revoke() or False),
            )
        assert service._reacquire_digital_loan_impl(loan) == _expected()
    finally:
        service.close()


@pytest.mark.parametrize("provider", ["paris", "synthetic-pnb"])
def test_explicit_recovery_with_stable_owner_preserves_borrower_admission(
    tmp_path, monkeypatch, provider,
):
    service = _setup(tmp_path, monkeypatch)
    try:
        service.storage.provider_connection_put({
            "provider_id": provider, "credential_id": "credential-a",
            "identity_id": "default", "enabled": True,
        })
        monkeypatch.setattr(service, "can_download_loan", lambda _: True)
        if provider == "paris":
            monkeypatch.setattr(
                service, "_paris_adept_identity_for_loan",
                lambda *_a, **_kw: ("default", "adept_borrower_binding_required"),
            )
        else:
            # The real neutral borrower resolver requires the exact active
            # account and never inherits a UI-selected borrower.
            service.accounts = [Account(
                provider_id=provider, credential_id="credential-a",
                identity_id="default", unique_id="", is_primary=True,
            )]
            monkeypatch.setattr(
                service, "_persist_active_loans",
                lambda *_a, **_kw: None,
            )
            monkeypatch.setattr(
                service, "_acquire_active_loan_automatically",
                lambda *_a, **_kw: {"status": "complete"},
            )
        receipt = service._reacquire_digital_loan_impl(_loan(provider))
        if provider == "paris":
            assert receipt["code"] == "adept_borrower_binding_required"
        else:
            assert receipt["status"] == "complete"
    finally:
        service.close()
