"""P0 #5975: durable source-owner epoch across provider and ADEPT staging.

Disposable SQLite is the authority. No provider HTTP, DRM fulfillment,
historical v15 promotion, entitlement grant, or background acquisition.
"""
from __future__ import annotations

import sqlite3
from unittest.mock import Mock

import pytest

from cifereca.core.loan_acquisition_admission import resolve
from cifereca.storage import CURRENT_SCHEMA_VERSION, Storage
from tests.test_automatic_loan_admission import (
    LOGGER, _forbid_work, _setup as automatic_setup,
)
from tests.test_core_p0_1120_adept_profile_guard_extraction import _fixture


def _connection(storage, *, provider="synthetic-pnb", owner="identity-a",
                enabled=True, credential="card-1"):
    return storage.provider_connection_put({
        "provider_id": provider, "credential_id": credential,
        "identity_id": owner, "enabled": enabled,
    })


@pytest.mark.parametrize("provider", ["paris", "synthetic-pnb", "another-provider"])
@pytest.mark.parametrize("cycle", ["owner_aba", "enabled_aba", "delete_recreate"])
def test_v18_epoch_is_monotone_across_every_source_aba(
    tmp_path, provider, cycle,
):
    path = tmp_path / "epoch.sqlite3"
    storage = Storage(path)
    assert storage.schema_version() == CURRENT_SCHEMA_VERSION == 18
    _connection(storage, provider=provider)
    first = storage.source_connection_epoch_get(provider, "card-1", "identity-a")
    assert type(first) is int and first >= 1

    if cycle == "owner_aba":
        _connection(storage, provider=provider, owner="identity-b")
        assert storage.source_connection_epoch_get(
            provider, "card-1", "identity-a",
        ) is None
        _connection(storage, provider=provider)
    elif cycle == "enabled_aba":
        _connection(storage, provider=provider, enabled=False)
        assert storage.source_connection_epoch_get(
            provider, "card-1", "identity-a",
        ) is None
        _connection(storage, provider=provider)
    else:
        with storage._lock, storage._connect() as db:
            db.execute(
                "DELETE FROM provider_connections "
                "WHERE provider_id=? AND credential_id=?",
                (provider, "card-1"),
            )
        assert storage.source_connection_epoch_get(
            provider, "card-1", "identity-a",
        ) is None
        _connection(storage, provider=provider)

    current = storage.source_connection_epoch_get(provider, "card-1", "identity-a")
    assert type(current) is int and current > first
    assert storage.provider_connection_owner_current(
        provider, "card-1", "identity-a",
    ) is True  # A conventional owner check cannot detect ABA.
    storage.close()
    again = Storage(path)
    try:
        assert again.source_connection_epoch_get(
            provider, "card-1", "identity-a",
        ) == current
    finally:
        again.close()


def test_epoch_updates_for_raw_sql_owner_switch_but_not_metadata_or_noop(tmp_path):
    store = Storage(tmp_path / "epoch.sqlite3")
    try:
        _connection(store)
        before = store.source_connection_epoch_get(
            "synthetic-pnb", "card-1", "identity-a",
        )
        with store._lock, store._connect() as db:
            db.execute(
                "UPDATE provider_connections SET metadata_json='{}' "
                "WHERE provider_id='synthetic-pnb' AND credential_id='card-1'"
            )
            db.execute(
                "UPDATE provider_connections SET identity_id=identity_id "
                "WHERE provider_id='synthetic-pnb' AND credential_id='card-1'"
            )
        _connection(store)  # no-op saved configuration also keeps the epoch
        assert store.source_connection_epoch_get(
            "synthetic-pnb", "card-1", "identity-a",
        ) == before

        with store._lock, store._connect() as db:
            db.execute(
                "UPDATE provider_connections SET identity_id='identity-b' "
                "WHERE provider_id='synthetic-pnb' AND credential_id='card-1'"
            )
            db.execute(
                "UPDATE provider_connections SET identity_id='identity-a' "
                "WHERE provider_id='synthetic-pnb' AND credential_id='card-1'"
            )
        assert store.source_connection_epoch_get(
            "synthetic-pnb", "card-1", "identity-a",
        ) == before + 2
    finally:
        store.close()


def test_epoch_is_provider_credential_scoped_and_persistent_tombstone(tmp_path):
    store = Storage(tmp_path / "epoch.sqlite3")
    try:
        _connection(store)
        other = _connection(store, provider="other-provider", credential="card-1")
        before = store.source_connection_epoch_get(
            "synthetic-pnb", "card-1", "identity-a",
        )
        _connection(store, provider="other-provider", owner="identity-b")
        assert store.source_connection_epoch_get(
            "synthetic-pnb", "card-1", "identity-a",
        ) == before
        assert other
        with store._lock, store._connect() as db:
            with pytest.raises(sqlite3.IntegrityError):
                db.execute(
                    "DELETE FROM provider_connection_epochs "
                    "WHERE provider_id='synthetic-pnb' AND credential_id='card-1'"
                )
            with pytest.raises(sqlite3.IntegrityError):
                db.execute(
                    "UPDATE provider_connection_epochs SET epoch=1 "
                    "WHERE provider_id='synthetic-pnb' AND credential_id='card-1'"
                )
        assert store.source_connection_epoch_get(
            "synthetic-pnb", "card-1", "identity-a",
        ) == before
    finally:
        store.close()


class Hostile(str):
    def casefold(self):
        raise AssertionError("subclass string must not enter SQLite")


@pytest.mark.parametrize("field,bad", [
    ("provider_id", Hostile("synthetic-pnb")),
    ("credential_id", Hostile("card-1")),
    ("identity_id", Hostile("identity-a")),
    ("provider_id", True),
    ("credential_id", None),
    ("identity_id", "identity\nother"),
    ("provider_id", ""),
])
def test_epoch_reader_refuses_malformed_scope_before_sql(
    tmp_path, monkeypatch, field, bad,
):
    store = Storage(tmp_path / "epoch.sqlite3")
    try:
        def forbidden():
            pytest.fail("malformed source reached SQLite")
        monkeypatch.setattr(store, "_connect", forbidden)
        args = {
            "provider_id": "synthetic-pnb",
            "credential_id": "card-1",
            "identity_id": "identity-a",
        }
        args[field] = bad
        assert store.source_connection_epoch_get(**args) is None
    finally:
        store.close()


@pytest.mark.parametrize("provider", ["paris", "synthetic-pnb"])
@pytest.mark.parametrize("stage", ["borrower", "profile", "profile_inventory"])
def test_poststage_owner_aba_is_refused_despite_identical_final_owner(
    tmp_path, provider, stage,
):
    storage = Storage(tmp_path / "epoch.sqlite3")
    try:
        _connection(storage, provider=provider, owner="identity-1",
                    credential="reader")
        admitted_epoch = storage.source_connection_epoch_get(
            provider, "reader", "identity-1",
        )
        invoke, receipt, borrower, resolver, profiles, loan, _ = _fixture(
            provider=provider,
            owner_current=storage.provider_connection_owner_current,
            source_epoch_get=storage.source_connection_epoch_get,
            source_epoch=admitted_epoch,
        )
        def aba():
            _connection(storage, provider=provider, owner="identity-b",
                        credential="reader")
            _connection(storage, provider=provider, owner="identity-1",
                        credential="reader")
        if stage == "borrower" and provider == "paris":
            borrower.side_effect = lambda *_a, **_kw: (aba() or ("identity-1", None))
        elif stage == "profile_inventory":
            profiles.side_effect = lambda: (aba() or [
                {"name": "reader-a", "fetch_capable": True},
            ])
        else:
            resolver.side_effect = lambda *_a: (aba() or ("reader-a", "binding"))
        assert invoke() == {
            "status": "attention_required", "loan_id": "loan-1",
            "code": "adept_borrower_ambiguous", "retry": "explicit_only",
        }
        assert storage.provider_connection_owner_current(
            provider, "reader", "identity-1",
        ) is True
        assert storage.source_connection_epoch_get(
            provider, "reader", "identity-1",
        ) > admitted_epoch
        receipt.assert_called_once()
        assert receipt.call_args.args[0]["metadata"]["acsm_staging_path"].endswith(
            "staged.acsm"
        )
        assert receipt.call_args.args[0]["attention_code"] == "adept_borrower_ambiguous"
    finally:
        storage.close()


@pytest.mark.parametrize("invalid", [True, 1.0, "1", None])
def test_poststage_epoch_reader_cannot_coerce_non_native_witness(invalid):
    probe = Mock(return_value=invalid)
    invoke, receipt, borrower, resolver, profiles, _, _ = _fixture(
        provider="synthetic-pnb", source_epoch_get=probe,
    )
    assert invoke()["code"] == "adept_borrower_ambiguous"
    borrower.assert_not_called()
    resolver.assert_not_called()
    receipt.assert_called_once()


def test_automatic_admission_rejects_owner_aba_during_rights_inventory(
    tmp_path, monkeypatch,
):
    service, loan = automatic_setup(tmp_path)
    try:
        original = service.storage.entitlements
        def inventory(*args, **kwargs):
            rows = original(*args, **kwargs)
            service.storage.provider_connection_put({
                "provider_id": "paris", "credential_id": "source-a",
                "identity_id": "alex", "enabled": True,
            })
            service.storage.provider_connection_put({
                "provider_id": "paris", "credential_id": "source-a",
                "identity_id": "default", "enabled": True,
            })
            return rows
        monkeypatch.setattr(service.storage, "entitlements", inventory)
        _forbid_work(service, monkeypatch)
        refusal = resolve(service, loan, _TRACE_LOG=LOGGER).refusal
        assert refusal == {
            "status": "attention_required", "loan_id": loan.id,
            "code": "adept_identity_unproven", "reason": "source_scope_changed",
            "retry": "explicit_only",
        }
        assert service.storage.provider_connection_owner_current(
            "paris", "source-a", "default",
        ) is True
    finally:
        service.close()
