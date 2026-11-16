"""P0 #5975: a staged ACSM cannot inherit a stale/disabled SQLite source owner.

Synthetic providers, disposable real SQLite, and mock ADEPT profile hooks only.
No provider requests, license fulfillment, grant write, or artifact deletion.
"""
from __future__ import annotations

from unittest.mock import Mock

import pytest

from cifereca.storage import Storage
from tests.test_core_p0_1120_adept_profile_guard_extraction import _fixture


class HostileString(str):
    def casefold(self):
        raise AssertionError("hostile source must never be normalized")


class Hostile:
    def __str__(self):
        raise AssertionError("untyped provenance must not be coerced")

    def __eq__(self, _other):
        raise AssertionError("untyped provenance must not be compared")


@pytest.mark.parametrize("provider", ["paris", "synthetic-pnb", "another-library"])
@pytest.mark.parametrize("change", ["none", "disabled", "rebound", "deleted"])
def test_source_owner_fence_reads_enabled_exact_connection_atomically(
    tmp_path, provider, change,
):
    storage = Storage(tmp_path / "owner.sqlite3")
    if change != "none":
        storage.provider_connection_put({
            "provider_id": provider, "credential_id": "card-1",
            "identity_id": "identity-1", "enabled": True,
        })
    if change == "disabled":
        storage.provider_connection_put({
            "provider_id": provider, "credential_id": "card-1",
            "identity_id": "identity-1", "enabled": False,
        })
    elif change == "rebound":
        storage.provider_connection_put({
            "provider_id": provider, "credential_id": "card-1",
            "identity_id": "identity-2", "enabled": True,
        })
    elif change == "deleted":
        with storage._lock, storage._connect() as db:
            db.execute(
                "DELETE FROM provider_connections WHERE provider_id=? "
                "AND credential_id=?", (provider, "card-1"),
            )
    assert storage.provider_connection_owner_current(
        provider, "card-1", "identity-1",
    ) is False
    if change == "none":
        storage.provider_connection_put({
            "provider_id": provider, "credential_id": "card-1",
            "identity_id": "identity-1", "enabled": True,
        })
        assert storage.provider_connection_owner_current(
            provider, "card-1", "identity-1",
        ) is True


@pytest.mark.parametrize("value", [
    None, False, Hostile(), HostileString("card-1"), "", "bad\nsource",
])
@pytest.mark.parametrize("field", ["provider", "credential", "identity"])
def test_owner_fence_refuses_malformed_coordinates_without_storage(
    tmp_path, monkeypatch, field, value,
):
    storage = Storage(tmp_path / "owner.sqlite3")
    def forbidden(*_args, **_kw):
        pytest.fail("malformed native provenance reached SQLite")
    monkeypatch.setattr(storage, "_connect", forbidden)
    scope = dict(provider_id="synthetic-pnb", credential_id="card-1",
                 identity_id="identity-1")
    scope[{"provider": "provider_id", "credential": "credential_id",
           "identity": "identity_id"}[field]] = value
    assert storage.provider_connection_owner_current(**scope) is False


@pytest.mark.parametrize("provider", ["paris", "synthetic-pnb"])
@pytest.mark.parametrize("outcome", [False, None, 1, "true"])
def test_staged_adept_refuses_missing_or_non_native_owner_evidence(
    provider, outcome,
):
    probe = Mock(return_value=outcome)
    invoke, store, borrower, resolver, profiles, loan, _ = _fixture(
        provider=provider, owner_current=probe,
    )
    assert invoke() == {
        "status": "attention_required", "loan_id": loan.id,
        "code": "adept_borrower_ambiguous", "retry": "explicit_only",
    }
    probe.assert_called_once_with(provider, "reader", "identity-1")
    borrower.assert_not_called()
    resolver.assert_not_called()
    profiles.assert_not_called()
    store.assert_called_once()
    assert store.call_args.args[0]["attention_code"] == "adept_borrower_ambiguous"
    assert store.call_args.args[0]["metadata"]["acsm_staging_path"].endswith("staged.acsm")


@pytest.mark.parametrize("stage", ["borrower", "profile", "inventory"])
@pytest.mark.parametrize("provider", ["paris", "synthetic-pnb"])
def test_revoked_source_during_poststage_callbacks_never_reaches_adept(
    stage, provider,
):
    state = {"current": True}
    probe = Mock(side_effect=lambda *_args: state["current"])
    invoke, store, borrower, resolver, profiles, loan, _ = _fixture(
        provider=provider, owner_current=probe,
    )

    def revoke(*_args, **_kwargs):
        state["current"] = False

    if stage == "borrower":
        # All staged ACSM sources now pass the same borrower callback.
        # Revocation during that callback must veto even its positive owner.
        borrower.side_effect = lambda *_a, **_kw: (revoke() or ("identity-1", None))
    elif stage == "profile":
        resolver.side_effect = lambda *_a: (revoke() or ("reader-a", "binding"))
    else:
        profiles.side_effect = lambda: (revoke() or [
            {"name": "reader-a", "fetch_capable": True},
        ])
    assert invoke() == {
        "status": "attention_required", "loan_id": loan.id,
        "code": "adept_borrower_ambiguous", "retry": "explicit_only",
    }
    assert state["current"] is False
    assert probe.call_count >= 2
    store.assert_called_once()
    assert store.call_args.args[0]["attention_code"] == "adept_borrower_ambiguous"


def test_sqlite_rebind_after_staging_revokes_existing_profile_proof(tmp_path):
    storage = Storage(tmp_path / "poststage.sqlite3")
    storage.provider_connection_put({
        "provider_id": "synthetic-pnb", "credential_id": "reader",
        "identity_id": "identity-1", "enabled": True,
    })
    probe = Mock(side_effect=storage.provider_connection_owner_current)
    invoke, store, borrower, resolver, profiles, loan, _ = _fixture(
        provider="synthetic-pnb", owner_current=probe,
    )
    def rebind(_identity):
        storage.provider_connection_put({
            "provider_id": "synthetic-pnb", "credential_id": "reader",
            "identity_id": "identity-2", "enabled": True,
        })
        return "reader-a", "explicit"
    resolver.side_effect = rebind
    assert invoke() == {
        "status": "attention_required", "loan_id": loan.id,
        "code": "adept_borrower_ambiguous", "retry": "explicit_only",
    }
    borrower.assert_called_once_with(loan, connection_identity_id="identity-1")
    profiles.assert_not_called()
    store.assert_called_once()


def test_sqlite_owner_unchanged_preserves_nonparis_profile_readiness(tmp_path):
    storage = Storage(tmp_path / "poststage.sqlite3")
    storage.provider_connection_put({
        "provider_id": "synthetic-pnb", "credential_id": "reader",
        "identity_id": "identity-1", "enabled": True,
    })
    invoke, store, borrower, resolver, profiles, loan, _ = _fixture(
        provider="synthetic-pnb",
        owner_current=storage.provider_connection_owner_current,
    )
    assert invoke() == ("identity-1", "reader-a")
    borrower.assert_called_once_with(loan, connection_identity_id="identity-1")
    resolver.assert_called_once_with("identity-1")
    store.assert_not_called()



@pytest.mark.parametrize("provider", ["synthetic-pnb", "another-library"])
@pytest.mark.parametrize("stage", ["initial_disabled", "identity_rebind", "rights_disable", "acquisitions_rebind"])
def test_automatic_admission_fences_live_owner_before_provider_or_ledger_write(
    tmp_path, monkeypatch, provider, stage,
):
    from cifereca.core.loan_acquisition_admission import resolve
    from cifereca.models import Account, Loan
    from tests.test_automatic_loan_admission import LOGGER, _forbid_work
    from tests.test_sync_state_contract import _service

    service = _service(tmp_path)
    # Exact active primary account, required by the now universal borrower gate.
    service.accounts = [Account(
        provider_id=provider, credential_id="reader", unique_id="",
        identity_id="default", is_primary=True,
    )]
    try:
        service.storage.provider_connection_put({
            "provider_id": provider, "credential_id": "reader",
            "identity_id": "default", "enabled": stage != "initial_disabled",
        })
        loan = Loan(
            id="loan-1", record_id="edition-1", provider="PNB",
            medium="digital", title="Automatic owner fence",
            source_provider=provider, source_credential="reader",
            source_account_id="primary", identity_id="default",
        )
        if stage == "identity_rebind":
            original = service.identity_by_id
            def rebind_on_identity(owner):
                result = original(owner)
                service.storage.provider_connection_put({
                    "provider_id": provider, "credential_id": "reader",
                    "identity_id": "alex", "enabled": True,
                })
                return result
            monkeypatch.setattr(service, "identity_by_id", rebind_on_identity)
        elif stage in ("rights_disable", "acquisitions_rebind"):
            hook = "entitlements" if stage == "rights_disable" else "acquisitions"
            previous = getattr(service.storage, hook)
            def revoke_after_inventory(*args, **kwargs):
                inventory = previous(*args, **kwargs)
                service.storage.provider_connection_put({
                    "provider_id": provider, "credential_id": "reader",
                    "identity_id": ("alex" if stage == "acquisitions_rebind"
                                    else "default"),
                    "enabled": stage != "rights_disable",
                })
                return inventory
            monkeypatch.setattr(service.storage, hook, revoke_after_inventory)
        _forbid_work(service, monkeypatch)
        result = resolve(service, loan, _TRACE_LOG=LOGGER)
        assert result.identity is None
        assert result.entitlement_id is None
        assert result.refusal == {
            "status": "attention_required", "loan_id": "loan-1",
            "code": "adept_identity_unproven",
            "reason": "source_scope_changed", "retry": "explicit_only",
        }
    finally:
        service.close()


def test_live_sqlite_source_owner_does_not_prevent_normal_automatic_admission(
    tmp_path, monkeypatch,
):
    from cifereca.core.loan_acquisition_admission import resolve
    from cifereca.models import Account, Loan
    from tests.test_automatic_loan_admission import LOGGER, _forbid_work
    from tests.test_sync_state_contract import _service

    service = _service(tmp_path)
    # Exact active primary account, required by the now universal borrower gate.
    service.accounts = [Account(
        provider_id="synthetic-pnb", credential_id="reader", unique_id="",
        identity_id="default", is_primary=True,
    )]
    try:
        service.storage.provider_connection_put({
            "provider_id": "synthetic-pnb", "credential_id": "reader",
            "identity_id": "default", "enabled": True,
        })
        loan = Loan(
            id="loan-1", record_id="edition-1", provider="PNB",
            medium="digital", title="Automatic owner fence",
            source_provider="synthetic-pnb", source_credential="reader",
            source_account_id="primary", identity_id="default",
        )
        _forbid_work(service, monkeypatch)
        result = resolve(service, loan, _TRACE_LOG=LOGGER)
        assert result.refusal is None
        assert result.identity.identity_id == "default"
        assert result.entitlement_id is None
    finally:
        service.close()
nkeypatch)
        result = resolve(service, loan, _TRACE_LOG=LOGGER)
        assert result.refusal is None
        assert result.identity.identity_id == "default"
        assert result.entitlement_id is None
    finally:
        service.close()
