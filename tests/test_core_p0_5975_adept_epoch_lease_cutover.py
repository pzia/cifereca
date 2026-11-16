"""P0 #5975: the original SQLite epoch survives every ADEPT stage.

Real SQLite for durable owner transitions, synthetic providers, isolated
ADEPT hooks. Never issue an HTTP request, profile execution or implicit grant.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from cifereca.core.loan_acquisition_admission import resolve
from cifereca.core.loan_source_epoch import SourceEpochLease
from cifereca.core.loan_adept_fulfillment import SourceEpochRevoked, fulfill
from cifereca.core.loan_safe_trace import trace_integer
from cifereca.models import Account, Loan
from tests.test_automatic_loan_admission import LOGGER, _forbid_work
from tests.test_core_p0_1120_adept_profile_guard_extraction import _fixture
from tests.test_sync_state_contract import _service


def _setup(tmp_path):
    service = _service(tmp_path)
    service.storage.provider_connection_put({
        "provider_id": "synthetic-pnb", "credential_id": "source-a",
        "identity_id": "default", "enabled": True,
    })
    service.accounts = [
        Account(
            provider_id="synthetic-pnb", credential_id="source-a",
            identity_id="default", unique_id="", is_primary=True,
        ),
    ]
    loan = Loan(
        id="loan-1", record_id="edition-1", provider="PNB",
        medium="digital", title="Synthetic",
        source_provider="synthetic-pnb", source_credential="source-a",
        source_account_id="primary", identity_id="default",
    )
    return service, loan


def _aba(service):
    for owner in ("alex", "default"):
        service.storage.provider_connection_put({
            "provider_id": "synthetic-pnb", "credential_id": "source-a",
            "identity_id": owner, "enabled": True,
        })


def _expected():
    return {
        "status": "attention_required", "loan_id": "loan-1",
        "code": "adept_identity_unproven",
        "reason": "source_scope_changed", "retry": "explicit_only",
    }


def test_stable_automatic_admission_carries_exact_native_epoch(tmp_path, monkeypatch):
    service, loan = _setup(tmp_path)
    try:
        _forbid_work(service, monkeypatch)
        admitted = resolve(service, loan, _TRACE_LOG=LOGGER)
        assert admitted.refusal is None
        assert admitted.identity.identity_id == "default"
        assert type(admitted.source_lease) is SourceEpochLease
        assert admitted.source_lease.current(service.storage, loan)
        assert admitted.source_lease.loan_id == "loan-1"
        assert admitted.source_lease.record_id == "edition-1"
        assert admitted.source_lease.identity_id == "default"
        assert admitted.source_lease.account_id == "primary"
        assert admitted.source_lease.epoch > 0
    finally:
        service.close()


@pytest.mark.parametrize("callback", ["identity_by_id", "entitlements", "acquisitions"])
@pytest.mark.parametrize("rotation", ["owner_aba", "disable_restore", "delete_recreate"])
def test_automatic_admission_rejects_aba_after_first_epoch_capture(
    tmp_path, monkeypatch, callback, rotation,
):
    service, loan = _setup(tmp_path)
    try:
        previous = (getattr(service, callback) if callback == "identity_by_id"
                    else getattr(service.storage, callback))
        def revoke():
            if rotation == "owner_aba":
                _aba(service)
            elif rotation == "disable_restore":
                for enabled in (False, True):
                    service.storage.provider_connection_put({
                        "provider_id": "synthetic-pnb",
                        "credential_id": "source-a",
                        "identity_id": "default", "enabled": enabled,
                    })
            else:
                with service.storage._lock, service.storage._connect() as db:
                    db.execute(
                        "DELETE FROM provider_connections "
                        "WHERE provider_id=? AND credential_id=?",
                        ("synthetic-pnb", "source-a"),
                    )
                service.storage.provider_connection_put({
                    "provider_id": "synthetic-pnb",
                    "credential_id": "source-a",
                    "identity_id": "default", "enabled": True,
                })
        def rotate(*args, **kwargs):
            result = previous(*args, **kwargs)
            revoke()
            return result
        target = service if callback == "identity_by_id" else service.storage
        monkeypatch.setattr(target, callback, rotate)
        _forbid_work(service, monkeypatch)
        refused = resolve(service, loan, _TRACE_LOG=LOGGER)
        assert refused.refusal == _expected()
        assert refused.identity is None
        assert refused.source_lease is None
        assert service.storage.acquisitions(provider_id="synthetic-pnb") == []
    finally:
        service.close()


@pytest.mark.parametrize("provider", ["paris", "synthetic-pnb"])
@pytest.mark.parametrize("hook", ["borrower", "profile", "inventory"])
def test_poststage_epoch_revocation_before_adept_produces_exact_attention_receipt(
    provider, hook,
):
    live = {"valid": True}
    witness = Mock(side_effect=lambda *_args: live["valid"])
    invoke, store, borrower, resolver, inventory, loan, options = _fixture(
        provider=provider, source_epoch_current=witness,
    )
    def revoke():
        live["valid"] = False
    if hook == "borrower":
        borrower.side_effect = lambda *_args, **_kw: (revoke() or ("identity-1", None))
    elif hook == "profile":
        resolver.side_effect = lambda *_args: (revoke() or ("reader-a", "binding"))
    else:
        inventory.side_effect = lambda: (revoke() or [{"name": "reader-a"}])
    assert invoke() == {
        "status": "attention_required", "loan_id": "loan-1",
        "code": "adept_borrower_ambiguous", "retry": "explicit_only",
    }
    assert witness.call_count > 1
    store.assert_called_once()
    receipt = store.call_args.args[0]
    assert receipt["attention_code"] == "adept_borrower_ambiguous"
    assert receipt["metadata"]["acsm_staging_path"] == "/synthetic/staged.acsm"
    assert receipt["provider_id"] == provider
    if hook == "borrower":
        resolver.assert_not_called()
        inventory.assert_not_called()
    elif hook == "profile":
        inventory.assert_not_called()


@pytest.mark.parametrize("rotation", ["owner_aba", "disable_restore"])
def test_poststage_uses_original_epoch_not_refresh_after_sqlite_rotation(
    tmp_path, rotation,
):
    from cifereca.storage import Storage

    store = Storage(tmp_path / "epoch.sqlite3")
    try:
        def put(owner, enabled=True):
            store.provider_connection_put({
                "provider_id": "synthetic-pnb", "credential_id": "reader",
                "identity_id": owner, "enabled": enabled,
            })
        put("identity-1")
        old_epoch = store.provider_connection_epoch(
            "synthetic-pnb", "reader", "identity-1",
        )
        invoke, ledger, borrower, resolver, profiles, loan, opts = _fixture(
            provider="synthetic-pnb",
            source_epoch_current=store.provider_connection_epoch_current,
        )
        # Mimic the exact admission-time generation rather than one captured
        # after the staged ACSM was already exposed to a provider callback.
        opts["source_lease"] = SourceEpochLease(
            "synthetic-pnb", "reader", "primary", "loan-1",
            "edition-1", "identity-1", old_epoch,
        )
        if rotation == "owner_aba":
            put("other-identity")
            put("identity-1")
        else:
            put("identity-1", enabled=False)
            put("identity-1", enabled=True)
        assert store.provider_connection_epoch(
            "synthetic-pnb", "reader", "identity-1",
        ) > old_epoch
        assert invoke()["code"] == "adept_borrower_ambiguous"
        borrower.assert_not_called()
        resolver.assert_not_called()
        ledger.assert_called_once()
    finally:
        store.close()


@pytest.mark.parametrize("result", [False, None, 0, "true"])
def test_final_adept_epoch_veto_never_executes_runtime(result):
    calls = []
    owner = NS(
        _originating_adept_profile_id=lambda *_: (calls.append("origin") or "profile-id"),
        execute_acquisition_request=lambda *_: pytest.fail("revoked ADEPT execution"),
        runtime_diagnostic=lambda *_: pytest.fail("revoked runtime diagnostic"),
    )
    with pytest.raises(SourceEpochRevoked):
        fulfill(
            owner, {"acsm_path": "PRIVATE"}, profile_name="profile-a",
            adept_borrower_identity_id="identity-1", trace_ref="a" * 20,
            time=NS(monotonic=lambda: 1.0),
            _TRACE_LOG=logging.getLogger("test.epoch.adept"),
            trace_integer=trace_integer,
            source_epoch_current=lambda: result,
        )
    assert calls == ["origin"]


def test_origin_profile_callback_revokes_lease_before_one_shot_adept():
    live = {"allowed": True}
    def origin(*_):
        live["allowed"] = False
        return "profile-a"
    owner = NS(
        _originating_adept_profile_id=origin,
        execute_acquisition_request=lambda *_: pytest.fail("ABA reached ADEPT"),
        runtime_diagnostic=lambda *_: pytest.fail("ABA reached diagnostics"),
    )
    with pytest.raises(SourceEpochRevoked):
        fulfill(
            owner, {"source": "PRIVATE"}, profile_name="profile-a",
            adept_borrower_identity_id="identity-1", trace_ref="a" * 20,
            time=NS(monotonic=lambda: 1.0),
            _TRACE_LOG=logging.getLogger("test.epoch.adept"),
            trace_integer=trace_integer,
            source_epoch_current=lambda: live["allowed"],
        )


def test_drift_from_loan_to_hostile_source_never_reaches_sqlite(tmp_path, monkeypatch):
    class Hostile:
        def __eq__(self, _):
            raise AssertionError("must not compare untrusted loan source")
        def __str__(self):
            raise AssertionError("must not stringify untrusted loan source")

    service, loan = _setup(tmp_path)
    try:
        lease = SourceEpochLease.capture(service.storage, loan, "default")
        assert type(lease) is SourceEpochLease
        def forbidden(*_a, **_kw):
            pytest.fail("stale/hostile source reached storage")
        monkeypatch.setattr(service.storage, "provider_connection_epoch_current", forbidden)
        loan.source_provider = Hostile()
        assert lease.current(service.storage, loan) is False
    finally:
        service.close()


@pytest.mark.parametrize("provider", ["paris", "synthetic-pnb"])
def test_poststage_epoch_valid_but_borrower_refused_cannot_reach_profile_or_adept(
    provider,
):
    """A valid source epoch is never an ADEPT borrower authorization."""
    invoke, store, borrower, resolver, inventory, loan, options = _fixture(
        provider=provider, borrower_code="adept_borrower_binding_required",
    )
    assert invoke() == {
        "status": "attention_required", "loan_id": "loan-1",
        "code": "adept_borrower_binding_required", "retry": "explicit_only",
    }
    borrower.assert_called_once_with(loan, connection_identity_id="identity-1")
    resolver.assert_not_called()
    inventory.assert_not_called()
    store.assert_called_once()
    receipt = store.call_args.args[0]
    assert receipt["attention_code"] == "adept_borrower_binding_required"
    assert receipt["metadata"]["acsm_staging_path"] == "/synthetic/staged.acsm"
    assert receipt["provider_id"] == provider


def test_synthetic_provider_automatic_admission_checks_borrower_before_rights(
    tmp_path, monkeypatch,
):
    service, loan = _setup(tmp_path)
    try:
        _forbid_work(service, monkeypatch)
        from unittest.mock import Mock
        borrower = Mock(return_value=(
            None, "adept_borrower_binding_required",
        ))
        monkeypatch.setattr(service, "_paris_adept_identity_for_loan", borrower)
        entitlement_lookup = Mock(side_effect=AssertionError(
            "borrower refusal must precede entitlement reads"
        ))
        monkeypatch.setattr(service.storage, "entitlements", entitlement_lookup)
        refused = resolve(service, loan, _TRACE_LOG=LOGGER)
        assert refused.identity is None
        assert refused.source_lease is None
        assert refused.refusal == {
            "status": "attention_required", "loan_id": "loan-1",
            "code": "adept_borrower_binding_required", "retry": "explicit_only",
        }
        borrower.assert_called_once_with(
            loan, connection_identity_id="default",
        )
        entitlement_lookup.assert_not_called()
        assert service.storage.acquisitions(provider_id="synthetic-pnb") == []
    finally:
        service.close()


@pytest.mark.parametrize("provider", ["paris", "synthetic-pnb"])
def test_poststage_borrower_resolver_exception_is_closed_refusal(provider):
    invoke, store, borrower, resolver, inventory, loan, opts = _fixture(
        provider=provider,
    )
    borrower.side_effect = RuntimeError("private untrusted lender payload")
    result = invoke()
    assert result == {
        "status": "attention_required", "loan_id": "loan-1",
        "code": "adept_borrower_ambiguous", "retry": "explicit_only",
    }
    resolver.assert_not_called()
    inventory.assert_not_called()
    store.assert_called_once()
    assert store.call_args.args[0]["attention_code"] == "adept_borrower_ambiguous"


@pytest.mark.parametrize(("field", "replacement"), [
    ("source_provider", "synthetic-other"),
    ("source_credential", "other-credential"),
    ("source_account_id", "other-borrower"),
    ("id", "other-loan"),
    ("record_id", "other-edition"),
])
def test_epoch_capture_refuses_source_mutation_inside_storage_reader(
    tmp_path, monkeypatch, field, replacement,
):
    """The lease cannot combine epoch(A) with callback-mutated coordinates(B)."""
    service, loan = _setup(tmp_path)
    try:
        # Deliberately assign the SAME epoch and owner to the foreign source:
        # a captured epoch alone cannot distinguish the old and new scope.
        service.storage.provider_connection_put({
            "provider_id": "synthetic-other", "credential_id": "source-a",
            "identity_id": "default", "enabled": True,
        })
        original = service.storage.provider_connection_epoch
        epoch_a = original("synthetic-pnb", "source-a", "default")
        assert epoch_a == original("synthetic-other", "source-a", "default")

        def stale_reader(provider, credential, identity):
            assert (provider, credential, identity) == (
                "synthetic-pnb", "source-a", "default",
            )
            epoch = original(provider, credential, identity)
            setattr(loan, field, replacement)
            return epoch

        monkeypatch.setattr(service.storage, "provider_connection_epoch", stale_reader)
        lease = SourceEpochLease.capture(service.storage, loan, "default")
        assert lease is None
        assert service.storage.acquisitions(provider_id="synthetic-pnb") == []
        assert service.storage.acquisitions(provider_id="synthetic-other") == []
    finally:
        service.close()


def test_epoch_capture_rejects_hostile_postread_coordinate_before_constructing_lease(
    tmp_path, monkeypatch,
):
    class Hostile:
        def __eq__(self, _other):
            raise AssertionError("foreign equality must not run")
        def __str__(self):
            raise AssertionError("foreign coercion must not run")

    service, loan = _setup(tmp_path)
    try:
        original = service.storage.provider_connection_epoch
        def contaminate(provider, credential, identity):
            result = original(provider, credential, identity)
            loan.source_provider = Hostile()
            return result
        monkeypatch.setattr(
            service.storage, "provider_connection_epoch", contaminate,
        )
        assert SourceEpochLease.capture(service.storage, loan, "default") is None
    finally:
        service.close()
