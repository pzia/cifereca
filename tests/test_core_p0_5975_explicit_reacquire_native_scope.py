"""P0 #5975: explicit reacquisition cannot retarget borrower/edition mid-flight.

Synthetic callbacks only: no provider GET, ADEPT execution or artifact rewrite.
The public successful/rejected recovery APIs stay unchanged.
"""
from __future__ import annotations

from contextlib import nullcontext
from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

import cifereca.services as services_module
from cifereca.core.explicit_reacquire_source import ExplicitReacquireSource
from cifereca.models import Loan
from cifereca.plugin_api import AttentionRequired
from tests.test_sync_state_contract import _service


class Hostile:
    def __str__(self):
        raise AssertionError("cannot coerce provider source")

    def __bool__(self):
        raise AssertionError("cannot truth-test provider source")

    def __eq__(self, _other):
        raise AssertionError("cannot compare provider source")


class HostileString(str):
    def casefold(self):
        raise AssertionError("cannot call a string-subclass method")


def _loan():
    return Loan(
        id="loan-a", record_id="edition-a", provider="PNB",
        medium="digital", title="Synthetic",
        source_provider="paris", source_credential="credential-a",
        source_account_id="primary", identity_id="default",
    )


def _setup(tmp_path, monkeypatch):
    service = _service(tmp_path)
    service.storage.provider_connection_put({
        "provider_id": "paris", "credential_id": "credential-a",
        "identity_id": "default",
    })
    monkeypatch.setattr(
        services_module, "InterProcessLock", lambda *_a: nullcontext(),
    )
    monkeypatch.setattr(
        services_module, "acquisition_lock_file",
        lambda: tmp_path / "explicit.lock",
    )
    return service


def _no_staging(service, monkeypatch):
    def forbidden(*_a, **_kw):
        pytest.fail("stale source reached durable staging/provider acquisition")
    monkeypatch.setattr(service, "_persist_active_loans", forbidden)
    monkeypatch.setattr(service, "_acquire_active_loan_automatically", forbidden)
    monkeypatch.setattr(service.storage, "acquisition_put", forbidden)
    monkeypatch.setattr(service.storage, "entitlement_put", forbidden)


@pytest.mark.parametrize(("field", "replacement"), [
    ("source_provider", "PARIS"),
    ("source_credential", "credential-b"),
    ("source_account_id", "other-reader"),
    ("id", "other-loan"),
    ("record_id", "other-edition"),
    ("medium", "physical"),
    ("id", Hostile()),
    ("source_provider", HostileString("paris")),
])
def test_eligibility_source_drift_prevents_owner_lookup(
    tmp_path, monkeypatch, field, replacement,
):
    service = _setup(tmp_path, monkeypatch)
    loan = _loan()
    try:
        def mutate_eligibility(_):
            setattr(loan, field, replacement)
            return True
        monkeypatch.setattr(service, "can_download_loan", mutate_eligibility)
        _no_staging(service, monkeypatch)
        monkeypatch.setattr(
            service.storage, "identity_for_connection",
            lambda *_a: pytest.fail("drift must be rejected before owner lookup"),
        )
        result = service._reacquire_digital_loan_impl(loan)
        assert result == {
            "status": "attention_required", "loan_id": "loan-a",
            "code": "adept_identity_unproven", "reason": "source_scope_changed",
            "retry": "explicit_only",
        }
    finally:
        service.close()


@pytest.mark.parametrize(("field", "replacement"), [
    ("source_provider", "other-provider"),
    ("source_credential", "credential-b"),
    ("source_account_id", "other-reader"),
    ("id", "other-loan"),
    ("record_id", "other-edition"),
    ("id", Hostile()),
])
@pytest.mark.parametrize("stage", [
    "owner_lookup", "identity_lookup", "borrower_grant",
    "conflict_inventory", "active_rights", "cross_identity",
    "prior_inventory", "persist",
])
def test_source_drift_in_any_callback_never_reaches_later_staging(
    tmp_path, monkeypatch, stage, field, replacement,
):
    service = _setup(tmp_path, monkeypatch)
    loan = _loan()
    try:
        monkeypatch.setattr(service, "can_download_loan", lambda _: True)
        original_owner = service.storage.identity_for_connection
        original_identity = service.identity_by_id
        stages = {"acquisitions": 0}

        def mutate(target):
            def wrapper(*args, **kwargs):
                result = target(*args, **kwargs)
                setattr(loan, field, replacement)
                return result
            return wrapper

        if stage == "owner_lookup":
            monkeypatch.setattr(service.storage, "identity_for_connection",
                                mutate(original_owner))
        elif stage == "identity_lookup":
            monkeypatch.setattr(service, "identity_by_id", mutate(original_identity))

        def grant(*_args, **_kwargs):
            if stage == "borrower_grant":
                setattr(loan, field, replacement)
            return "default", None
        monkeypatch.setattr(service, "_paris_adept_identity_for_loan", grant)
        original_acquisitions = service.storage.acquisitions

        def acquisitions(*args, **kwargs):
            stages["acquisitions"] += 1
            result = original_acquisitions(*args, **kwargs)
            if ((stage == "conflict_inventory" and stages["acquisitions"] == 1)
                    or (stage == "prior_inventory" and stages["acquisitions"] == 2)):
                setattr(loan, field, replacement)
            return result
        monkeypatch.setattr(service.storage, "acquisitions", acquisitions)
        original_entitlements = service.storage.entitlements
        if stage == "active_rights":
            monkeypatch.setattr(service.storage, "entitlements",
                                mutate(original_entitlements))

        def conflict(*_args, **_kwargs):
            if stage == "cross_identity":
                setattr(loan, field, replacement)
            return False
        monkeypatch.setattr(service, "_cross_identity_loan_conflict", conflict)
        if stage == "persist":
            def persist(*_a, **_kw):
                setattr(loan, field, replacement)
            monkeypatch.setattr(service, "_persist_active_loans", persist)
        else:
            _no_staging(service, monkeypatch)

        def no_provider(*_a, **_kw):
            pytest.fail("stale source reached automatic acquisition")
        monkeypatch.setattr(service, "_acquire_active_loan_automatically", no_provider)
        result = service._reacquire_digital_loan_impl(loan)
        assert result == {
            "status": "attention_required",
            "loan_id": "loan-a" if type(replacement) is not str or field != "id"
                       else "loan-a",
            "code": "adept_identity_unproven", "reason": "source_scope_changed",
            "retry": "explicit_only",
        }
    finally:
        service.close()


@pytest.mark.parametrize(("field", "value"), [
    ("source_provider", Hostile()),
    ("source_credential", HostileString("credential-a")),
    ("source_account_id", Hostile()),
    ("id", Hostile()),
    ("record_id", False),
    ("medium", Hostile()),
])
def test_malformed_source_never_selects_owner_or_grant(
    tmp_path, monkeypatch, field, value,
):
    service = _setup(tmp_path, monkeypatch)
    loan = replace(_loan(), **{field: value})
    try:
        monkeypatch.setattr(service, "can_download_loan", lambda _: True)
        monkeypatch.setattr(
            service.storage, "identity_for_connection",
            lambda *_a: pytest.fail("malformed source reached storage"),
        )
        _no_staging(service, monkeypatch)
        result = service._reacquire_digital_loan_impl(loan)
        assert result["code"] == "adept_identity_unproven"
        assert result["reason"] == "source_scope_unproven"
        assert result["loan_id"] is None
    finally:
        service.close()


def test_unchanged_source_preserves_explicit_borrower_refusal(
    tmp_path, monkeypatch,
):
    service = _setup(tmp_path, monkeypatch)
    try:
        monkeypatch.setattr(service, "can_download_loan", lambda _: True)
        monkeypatch.setattr(
            service, "_paris_adept_identity_for_loan",
            lambda *_a, **_kw: ("default", "adept_borrower_binding_required"),
        )
        _no_staging(service, monkeypatch)
        assert service._reacquire_digital_loan_impl(_loan()) == {
            "status": "attention_required", "loan_id": "loan-a",
            "code": "adept_borrower_binding_required", "retry": "explicit_only",
        }
    finally:
        service.close()


def test_exact_previous_row_only_may_receive_failure_metadata(
    tmp_path, monkeypatch,
):
    service = _setup(tmp_path, monkeypatch)
    loan = _loan()
    path = tmp_path / "preserved.epub"
    path.write_bytes(b"old")
    try:
        monkeypatch.setattr(service, "can_download_loan", lambda _: True)
        monkeypatch.setattr(service, "_paris_adept_identity_for_loan",
                            lambda *_a, **_kw: ("default", None))
        monkeypatch.setattr(service, "_cross_identity_loan_conflict",
                            lambda *_a, **_kw: False)
        monkeypatch.setattr(service, "_persist_active_loans",
                            lambda *_a, **_kw: None)
        monkeypatch.setattr(service, "_acquire_active_loan_automatically",
                            lambda *_a, **_kw: {
                                "status": "attention_required", "code": "synthetic",
                            })
        monkeypatch.setattr(services_module, "inspect_artifact",
                            lambda _path: NS(valid=True))
        exact = {
            "acquisition_id": "old-ok", "provider_id": "paris",
            "credential_id": "credential-a", "account_id": "primary",
            "identity_id": "default", "item_id": "edition-a",
            "status": "complete", "metadata": {"path": str(path)},
        }
        rows = [
            exact,
            {**exact, "acquisition_id": "other-account", "account_id": "other"},
            {**exact, "acquisition_id": "other-edition", "item_id": "other"},
            {**exact, "acquisition_id": "other-identity", "identity_id": "alex"},
            {**exact, "acquisition_id": "hostile-row", "account_id": Hostile()},
            {**exact, "acquisition_id": "corrupt-path", "metadata": {"path": Hostile()}},
        ]
        monkeypatch.setattr(service.storage, "acquisitions",
                            lambda **_kwargs: rows)
        saved = []
        monkeypatch.setattr(service.storage, "acquisition_put",
                            lambda row: saved.append(row))
        with pytest.raises(AttentionRequired) as rejected:
            service._reacquire_digital_loan_impl(loan)
        assert rejected.value.code == "synthetic"
        assert [r["acquisition_id"] for r in saved] == ["old-ok"]
        assert saved[0]["metadata"]["last_reacquisition_failure"]["code"] == "synthetic"
    finally:
        service.close()


def test_native_lease_requires_exact_provider_credential_borrower_loan_and_edition():
    loan = _loan()
    lease = ExplicitReacquireSource.capture(loan)
    assert lease is not None and lease.current(loan)
    assert lease.matches_previous({
        "provider_id": "paris", "credential_id": "credential-a",
        "account_id": "primary", "identity_id": "default",
        "item_id": "edition-a", "status": "complete",
    }, identity_id="default")
    loan.source_provider = "PARIS"
    assert not lease.current(loan)
