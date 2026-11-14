"""P0 #5851: automatic loan rights must stay bound to the storage owner.

These are offline admission-only regressions: no provider IO or artifact work.
"""
from dataclasses import replace

import pytest

from cifereca.core.loan_acquisition_admission import resolve
from tests.test_automatic_loan_admission import LOGGER, _setup


@pytest.mark.parametrize("wrong", ["alex", "", None])
def test_identity_lookup_mismatch_never_selects_another_owner(
    tmp_path, monkeypatch, wrong,
):
    service, loan = _setup(tmp_path)
    try:
        real = service.identity_by_id("default")
        forged = replace(real, identity_id=wrong) if wrong is not None else None
        monkeypatch.setattr(service, "identity_by_id", lambda _owner: forged)

        def forbid(*_args, **_kwargs):
            pytest.fail("unproven owner must never reach grants or rights")

        monkeypatch.setattr(service, "_paris_adept_identity_for_loan", forbid)
        monkeypatch.setattr(service.storage, "entitlements", forbid)
        monkeypatch.setattr(service.storage, "acquisitions", forbid)
        outcome = resolve(service, loan, _TRACE_LOG=LOGGER)
        assert outcome.identity is None and outcome.entitlement_id is None
        assert outcome.refusal == {
            "status": "attention_required", "loan_id": loan.id,
            "code": "adept_identity_unproven",
            "reason": "unbound_connection_identity", "retry": "explicit_only",
        }
    finally:
        service.close()


@pytest.mark.parametrize("owner", ["", "default-subclass"])
def test_non_native_owner_from_connection_lookup_never_falls_back_to_ui(
    tmp_path, monkeypatch, owner,
):
    service, loan = _setup(tmp_path)
    try:
        if owner == "default-subclass":
            class UntrustedOwner(str):
                def __eq__(self, _other):
                    raise AssertionError("untrusted owner equality invoked")
            owner = UntrustedOwner("default")
        monkeypatch.setattr(service.storage, "identity_for_connection",
                            lambda *_args: owner)

        def forbid(*_args, **_kwargs):
            pytest.fail("invalid owner must not reach identity, grants or rights")

        monkeypatch.setattr(service, "identity_by_id", forbid)
        monkeypatch.setattr(service, "_paris_adept_identity_for_loan", forbid)
        monkeypatch.setattr(service.storage, "entitlements", forbid)
        monkeypatch.setattr(service.storage, "acquisitions", forbid)
        outcome = resolve(service, loan, _TRACE_LOG=LOGGER)
        assert outcome.refusal["reason"] == "unbound_connection_identity"
    finally:
        service.close()


@pytest.mark.parametrize("hook", [
    "_paris_adept_identity_for_loan", "entitlements", "acquisitions",
])
def test_owner_drift_during_external_hook_cannot_retarget_entitlement(
    tmp_path, monkeypatch, hook,
):
    service, loan = _setup(tmp_path)
    try:
        selected = service.identity_by_id("default")
        owner = service if hook.startswith("_paris") else service.storage
        original = getattr(owner, hook)

        def drift(*args, **kwargs):
            result = original(*args, **kwargs)
            object.__setattr__(selected, "identity_id", "alex")
            return result

        monkeypatch.setattr(owner, hook, drift)
        refused = resolve(service, loan, _TRACE_LOG=LOGGER)
        assert refused.identity is None and refused.entitlement_id is None
        assert refused.refusal == {
            "status": "attention_required", "loan_id": loan.id,
            "code": "adept_identity_unproven",
            "reason": "source_scope_changed", "retry": "explicit_only",
        }
    finally:
        service.close()
