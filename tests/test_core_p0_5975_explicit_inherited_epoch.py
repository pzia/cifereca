"""P0 #5975: explicit recovery keeps one epoch into nested ACSM/ADEPT.

Disposable SQLite with the real source-epoch triggers; synthetic, inert
provider callbacks. Neither a grant, a ledger row nor an implicit GET can be
manufactured by a connection that returned to the same identity after ABA.
"""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from cifereca.core.loan_source_epoch import (
    SourceEpochLease, inherited_explicit_source_epoch,
    pin_explicit_source_epoch,
)
from tests.test_core_p0_5975_explicit_reacquire_native_scope import (
    _loan as paris_loan, _setup, _no_staging,
)


def _loan(provider: str):
    return replace(paris_loan(), source_provider=provider)


def _refusal():
    return {
        "status": "attention_required", "loan_id": "loan-a",
        "code": "adept_identity_unproven",
        "reason": "source_scope_changed", "retry": "explicit_only",
    }


def _rotate(service, provider: str, mode: str):
    def put(identity: str = "default", enabled: bool = True):
        service.storage.provider_connection_put({
            "provider_id": provider, "credential_id": "credential-a",
            "identity_id": identity, "enabled": enabled,
        })
    if mode == "owner_aba":
        put("alex")
        put("default")
    elif mode == "disable_restore":
        put(enabled=False)
        put(enabled=True)
    elif mode == "delete_recreate":
        with service.storage._lock, service.storage._connect() as db:
            db.execute(
                "DELETE FROM provider_connections "
                "WHERE provider_id=? AND credential_id=?",
                (provider, "credential-a"),
            )
        put()
    else:
        raise AssertionError("unknown rotation mode")


@pytest.mark.parametrize("provider,stage", [
    ("paris", "identity"),
    ("paris", "borrower"),
    ("paris", "conflict_inventory"),
    ("paris", "active_rights"),
    ("paris", "cross_identity"),
    ("paris", "prior_inventory"),
    ("paris", "persist"),
    ("paris", "nested_auto"),
    ("synthetic-pnb", "identity"),
    ("synthetic-pnb", "prior_inventory"),
    ("synthetic-pnb", "persist"),
    ("synthetic-pnb", "nested_auto"),
])
@pytest.mark.parametrize("mode", ["owner_aba", "disable_restore", "delete_recreate"])
def test_explicit_reacquire_refuses_revalidated_owner_after_hidden_aba(
    tmp_path, monkeypatch, provider, stage, mode,
):
    service = _setup(tmp_path, monkeypatch)
    loan = _loan(provider)
    try:
        service.storage.provider_connection_put({
            "provider_id": provider, "credential_id": "credential-a",
            "identity_id": "default", "enabled": True,
        })
        monkeypatch.setattr(service, "can_download_loan", lambda _: True)
        _no_staging(service, monkeypatch)
        rotated = {"done": False}

        def rotate():
            assert not rotated["done"]
            rotated["done"] = True
            _rotate(service, provider, mode)

        def wrap(callback):
            def invoke(*args, **kwargs):
                result = callback(*args, **kwargs)
                rotate()
                return result
            return invoke

        if stage == "identity":
            monkeypatch.setattr(
                service, "identity_by_id", wrap(service.identity_by_id),
            )
        if stage == "borrower":
            monkeypatch.setattr(
                service, "_paris_adept_identity_for_loan",
                wrap(lambda *_a, **_kw: ("default", None)),
            )
        elif provider == "paris":
            monkeypatch.setattr(
                service, "_paris_adept_identity_for_loan",
                lambda *_a, **_kw: ("default", None),
            )
        if stage in ("conflict_inventory", "prior_inventory"):
            prior = service.storage.acquisitions
            reads = {"n": 0}
            count = 1 if stage == "conflict_inventory" else (
                2 if provider == "paris" else 1
            )

            def inventory(*args, **kwargs):
                reads["n"] += 1
                rows = prior(*args, **kwargs)
                if reads["n"] == count:
                    rotate()
                return rows

            monkeypatch.setattr(service.storage, "acquisitions", inventory)
        if stage == "active_rights":
            monkeypatch.setattr(
                service.storage, "entitlements",
                wrap(service.storage.entitlements),
            )
        if stage == "cross_identity":
            monkeypatch.setattr(
                service, "_cross_identity_loan_conflict",
                wrap(lambda *_a, **_kw: False),
            )
        if stage == "persist":
            monkeypatch.setattr(
                service, "_persist_active_loans",
                lambda *_a, **_kw: rotate(),
            )
        if stage == "nested_auto":
            # Let execution reach the nested entrypoint, but never write a
            # real ledger or issue a provider request in this synthetic test.
            monkeypatch.setattr(
                service, "_persist_active_loans",
                lambda *_a, **_kw: None,
            )
            def nested(*_args, **kwargs):
                assert kwargs["force_refresh"] is True
                lease = inherited_explicit_source_epoch()
                assert type(lease) is SourceEpochLease
                assert lease.current(service.storage, loan)
                rotate()
                return {"status": "complete"}
            monkeypatch.setattr(
                service, "_acquire_active_loan_automatically", nested,
            )
        result = service._reacquire_digital_loan_impl(loan)
        assert rotated["done"] is True
        assert result == _refusal()
        # An ABA has restored the same visible owner but never the epoch.
        assert service.storage.identity_for_connection(
            provider, "credential-a",
        ) == "default"
        assert service.storage.acquisitions(provider_id=provider) == []
    finally:
        service.close()


@pytest.mark.parametrize("provider", ["paris", "synthetic-pnb"])
def test_stable_reacquisition_passes_original_epoch_to_nested_auto(
    tmp_path, monkeypatch, provider,
):
    service = _setup(tmp_path, monkeypatch)
    loan = _loan(provider)
    try:
        service.storage.provider_connection_put({
            "provider_id": provider, "credential_id": "credential-a",
            "identity_id": "default", "enabled": True,
        })
        monkeypatch.setattr(service, "can_download_loan", lambda _: True)
        monkeypatch.setattr(service, "_persist_active_loans",
                            lambda *_a, **_kw: None)
        if provider == "paris":
            monkeypatch.setattr(
                service, "_paris_adept_identity_for_loan",
                lambda *_a, **_kw: ("default", None),
            )
        seen = []
        def nested(*_args, **kwargs):
            lease = inherited_explicit_source_epoch()
            assert type(lease) is SourceEpochLease
            assert kwargs["force_refresh"] is True
            assert lease.current(service.storage, loan)
            assert lease.provider_id == provider
            assert lease.credential_id == "credential-a"
            assert lease.account_id == "primary"
            assert lease.loan_id == "loan-a"
            assert lease.record_id == "edition-a"
            assert lease.identity_id == "default"
            assert lease.epoch == service.storage.provider_connection_epoch(
                provider, "credential-a", "default",
            )
            seen.append(lease)
            return {"status": "complete"}
        monkeypatch.setattr(service, "_acquire_active_loan_automatically",
                            nested)
        assert service._reacquire_digital_loan_impl(loan) == {
            "status": "complete",
        }
        assert len(seen) == 1
        assert inherited_explicit_source_epoch() is None
    finally:
        service.close()


@pytest.mark.parametrize("provider", ["synthetic-pnb", "paris"])
@pytest.mark.parametrize("mode", ["owner_aba", "disable_restore", "delete_recreate"])
def test_nested_auto_loan_lock_cannot_refresh_explicit_source_lease(
    tmp_path, monkeypatch, provider, mode,
):
    service = _setup(tmp_path, monkeypatch)
    loan = _loan(provider)
    try:
        service.storage.provider_connection_put({
            "provider_id": provider, "credential_id": "credential-a",
            "identity_id": "default", "enabled": True,
        })
        lease = SourceEpochLease.capture(service.storage, loan, "default")
        assert type(lease) is SourceEpochLease
        _rotate(service, provider, mode)
        assert lease.current(service.storage, loan) is False
        monkeypatch.setattr(
            service, "_acquire_active_loan_automatically_guarded",
            lambda *_a, **_kw: pytest.fail("stale lease entered provider path"),
        )
        with pin_explicit_source_epoch(lease):
            result = service._acquire_active_loan_automatically(
                loan, force_refresh=True,
            )
        assert result == _refusal()
        assert inherited_explicit_source_epoch() is None
    finally:
        service.close()


@pytest.mark.parametrize("mode", ["owner_aba", "disable_restore"])
def test_nested_guarded_auto_rejects_old_epoch_even_if_new_admission_passes(
    tmp_path, monkeypatch, mode,
):
    service = _setup(tmp_path, monkeypatch)
    loan = _loan("synthetic-pnb")
    try:
        service.storage.provider_connection_put({
            "provider_id": "synthetic-pnb", "credential_id": "credential-a",
            "identity_id": "default", "enabled": True,
        })
        lease = SourceEpochLease.capture(service.storage, loan, "default")
        assert type(lease) is SourceEpochLease
        _rotate(service, "synthetic-pnb", mode)
        assert service.storage.provider_connection_epoch(
            "synthetic-pnb", "credential-a", "default",
        ) > lease.epoch
        monkeypatch.setattr(
            service, "_persist_active_loans",
            lambda *_a, **_kw: pytest.fail("stale epoch reached staging"),
        )
        monkeypatch.setattr(
            service.storage, "acquisition_put",
            lambda *_a, **_kw: pytest.fail("stale epoch reached ledger write"),
        )
        with pin_explicit_source_epoch(lease):
            assert service._acquire_active_loan_automatically_guarded(
                loan, force_refresh=True,
            ) == _refusal()
        assert inherited_explicit_source_epoch() is None
    finally:
        service.close()


def test_legacy_borrower_refusal_remains_authoritative_under_stable_epoch(
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
        assert service._reacquire_digital_loan_impl(paris_loan()) == {
            "status": "attention_required", "loan_id": "loan-a",
            "code": "adept_borrower_binding_required", "retry": "explicit_only",
        }
    finally:
        service.close()


@pytest.mark.parametrize("untrusted", [
    False, 0, 1, "1", [], NS(epoch=1),
])
def test_invalid_inherited_lease_cannot_enter_transaction_context(untrusted):
    with pytest.raises(ValueError, match="invalid_explicit_source_epoch"):
        with pin_explicit_source_epoch(untrusted):
            pytest.fail("untrusted lease entered nested automatic admission")
    assert inherited_explicit_source_epoch() is None


def test_recovery_context_is_always_restored_after_error(tmp_path, monkeypatch):
    service = _setup(tmp_path, monkeypatch)
    loan = paris_loan()
    try:
        lease = SourceEpochLease.capture(service.storage, loan, "default")
        assert type(lease) is SourceEpochLease
        with pytest.raises(RuntimeError, match="synthetic exception"):
            with pin_explicit_source_epoch(lease):
                assert inherited_explicit_source_epoch() is lease
                raise RuntimeError("synthetic exception")
        assert inherited_explicit_source_epoch() is None
    finally:
        service.close()


def test_conflicting_nested_transaction_cannot_replace_epoch(tmp_path, monkeypatch):
    service = _setup(tmp_path, monkeypatch)
    loan = paris_loan()
    try:
        lease = SourceEpochLease.capture(service.storage, loan, "default")
        assert type(lease) is SourceEpochLease
        other = replace(lease, epoch=lease.epoch + 1)
        with pin_explicit_source_epoch(lease):
            assert inherited_explicit_source_epoch() is lease
            with pytest.raises(ValueError, match="explicit_source_epoch_conflict"):
                with pin_explicit_source_epoch(other):
                    pytest.fail("another loan replaced the original epoch")
            assert inherited_explicit_source_epoch() is lease
        assert inherited_explicit_source_epoch() is None
    finally:
        service.close()


def test_forged_native_lease_epoch_is_rejected_before_comparison(
    tmp_path, monkeypatch,
):
    class Hostile:
        def __eq__(self, _other):
            pytest.fail("hostile grant epoch was compared")
        def __str__(self):
            pytest.fail("hostile epoch was formatted")
    service = _setup(tmp_path, monkeypatch)
    loan = paris_loan()
    try:
        lease = SourceEpochLease.capture(service.storage, loan, "default")
        assert type(lease) is SourceEpochLease
        forged = replace(lease, epoch=Hostile())
        with pin_explicit_source_epoch(forged):
            assert service._acquire_active_loan_automatically(
                loan, force_refresh=True,
            ) == _refusal()
        assert inherited_explicit_source_epoch() is None
    finally:
        service.close()
