"""P0 #5975: a v16 ADEPT grant's explicit authority cannot survive ABA.

SQLite real source/account/profile evidence and durable grant-generation
tombstones. No provider access, ADEPT execution, or implicit v15 migration.
"""
from __future__ import annotations

import sqlite3

import pytest

from cifereca.storage import CURRENT_SCHEMA_VERSION, Storage
from tests.test_core_p0_5851_generic_adept_grants_v16 import _persist_account
from tests.test_paris_borrower_adept_binding import service_with_linked_borrower


def _scope(provider="paris"):
    return dict(provider_id=provider, credential_id="alice-paris",
                account_id="linked-123")


def _evidence(provider="paris"):
    return dict(**_scope(provider), connection_identity_id="alice",
                borrower_identity_id="bob", profile_id="bob-profile")


def _setup(tmp_path, provider="paris"):
    service, _loan = service_with_linked_borrower(tmp_path)
    if provider != "paris":
        service.storage.provider_connection_put({
            "identity_id": "alice", "provider_id": provider,
            "credential_id": "alice-paris", "enabled": True,
        })
    _persist_account(service.storage, provider=provider)
    return service


def _confirm(store, provider="paris"):
    store.adept_borrower_grant_put(
        **_evidence(provider), confirmed=True,
    )


def _generation(store, provider="paris"):
    return store.adept_borrower_grant_generation(**_evidence(provider))


@pytest.mark.parametrize("provider", ["paris", "synthetic-pnb"])
def test_exact_v16_grant_generation_requires_positive_live_attestation(
    tmp_path, provider,
):
    service = _setup(tmp_path, provider)
    store = service.storage
    try:
        assert store.schema_version() == CURRENT_SCHEMA_VERSION == 17
        assert _generation(store, provider) is None
        _confirm(store, provider)
        current = _generation(store, provider)
        assert type(current) is int and current > 0
        assert store.adept_borrower_grant_generation_current(
            **_evidence(provider), expected_generation=current,
        ) is True
        assert store.adept_borrower_grant_generation_current(
            **_evidence(provider), expected_generation=current + 1,
        ) is False
        assert store.adept_borrower_grant_generation(
            **{**_evidence(provider), "borrower_identity_id": "alice"}
        ) is None
        assert store.adept_borrower_grant_generation(
            **{**_evidence(provider), "profile_id": "some-other-profile"}
        ) is None
        assert store.adept_borrower_grant_generation(
            **{**_evidence(provider), "connection_identity_id": "bob"}
        ) is None
        assert store.adept_borrower_grant_generation(
            **{**_evidence(provider), "provider_id": "other"}
        ) is None
    finally:
        service.close()


@pytest.mark.parametrize("provider", ["paris", "synthetic-pnb"])
@pytest.mark.parametrize("change", ["delete_reconfirm", "repeat_confirmation",
                                   "raw_sql_update", "profile_rotate_reconfirm"])
def test_grant_generation_never_recovers_after_revoke_or_reconfirm(
    tmp_path, provider, change,
):
    service = _setup(tmp_path, provider)
    store = service.storage
    dbpath = store.path
    try:
        _confirm(store, provider)
        original = _generation(store, provider)
        assert original is not None
        if change == "delete_reconfirm":
            store.adept_borrower_grant_delete(**_scope(provider))
            assert _generation(store, provider) is None
            _confirm(store, provider)
        elif change == "repeat_confirmation":
            # A repeated explicit reconfirmation during the same wall-clock
            # second must still retire all earlier exact profile proofs.
            _confirm(store, provider)
        elif change == "raw_sql_update":
            with store._lock, store._connect() as db:
                db.execute(
                    "UPDATE adept_borrower_grants SET updated_at=updated_at "
                    "WHERE provider_id=? AND credential_id=? AND account_id=?",
                    tuple(_scope(provider).values()),
                )
        elif change == "profile_rotate_reconfirm":
            store.adept_profile_binding_set(
                "identity", "bob", "different-profile",
            )
            assert _generation(store, provider) is None
            store.adept_profile_binding_set("identity", "bob", "bob-profile")
            assert _generation(store, provider) is None
            _confirm(store, provider)
        current = _generation(store, provider)
        assert type(current) is int and current > original
        assert store.adept_borrower_grant_generation_current(
            **_evidence(provider), expected_generation=original,
        ) is False
        assert store.adept_borrower_grant_generation_current(
            **_evidence(provider), expected_generation=current,
        ) is True
        store.close()
        restarted = Storage(dbpath)
        try:
            assert restarted.schema_version() == 17
            assert _generation(restarted, provider) == current
            assert restarted.adept_borrower_grant_generation_current(
                **_evidence(provider), expected_generation=original,
            ) is False
        finally:
            restarted.close()
    finally:
        service.close()


def test_independent_provider_grants_cannot_revoke_one_another(tmp_path):
    service = _setup(tmp_path)
    store = service.storage
    try:
        store.provider_connection_put({
            "identity_id": "alice", "provider_id": "synthetic-pnb",
            "credential_id": "alice-paris", "enabled": True,
        })
        _persist_account(store, provider="synthetic-pnb")
        _confirm(store, "paris")
        _confirm(store, "synthetic-pnb")
        paris = _generation(store, "paris")
        other = _generation(store, "synthetic-pnb")
        assert paris is not None and other is not None
        store.adept_borrower_grant_delete(**_scope("paris"))
        assert _generation(store, "paris") is None
        assert _generation(store, "synthetic-pnb") == other
        _confirm(store, "paris")
        assert _generation(store, "paris") > paris
        assert _generation(store, "synthetic-pnb") == other
    finally:
        service.close()


@pytest.mark.parametrize("change", ["delete", "profile_revoke", "direct_sql_delete"])
def test_deleted_grant_preserves_generation_tombstone(tmp_path, change):
    service = _setup(tmp_path)
    store = service.storage
    try:
        _confirm(store)
        initial = _generation(store)
        if change == "delete":
            store.adept_borrower_grant_delete(**_scope())
        elif change == "profile_revoke":
            store.adept_profile_binding_set("identity", "bob", "different")
        else:
            with store._lock, store._connect() as db:
                db.execute(
                    "DELETE FROM adept_borrower_grants "
                    "WHERE provider_id=? AND credential_id=? AND account_id=?",
                    tuple(_scope().values()),
                )
        assert _generation(store) is None
        with store._lock, store._connect() as db:
            row = db.execute(
                "SELECT generation FROM adept_borrower_grant_generations "
                "WHERE provider_id=? AND credential_id=? AND account_id=?",
                tuple(_scope().values()),
            ).fetchone()
        assert type(row[0]) is int and row[0] > initial
    finally:
        service.close()


def test_raw_sql_cannot_rewind_generation_or_delete_tombstone(tmp_path):
    service = _setup(tmp_path)
    store = service.storage
    try:
        _confirm(store)
        first = _generation(store)
        with pytest.raises(sqlite3.IntegrityError, match="generation_rewind"):
            with store._lock, store._connect() as db:
                db.execute(
                    "UPDATE adept_borrower_grant_generations "
                    "SET generation=generation-1 "
                    "WHERE provider_id='paris' AND credential_id='alice-paris' "
                    "AND account_id='linked-123'"
                )
        with pytest.raises(sqlite3.IntegrityError, match="generation_tombstone"):
            with store._lock, store._connect() as db:
                db.execute(
                    "DELETE FROM adept_borrower_grant_generations "
                    "WHERE provider_id='paris' AND credential_id='alice-paris' "
                    "AND account_id='linked-123'"
                )
        assert _generation(store) == first
    finally:
        service.close()


def test_existing_v17_positive_grant_seeds_once_without_reconfirming(tmp_path):
    service = _setup(tmp_path)
    store = service.storage
    dbpath = store.path
    try:
        _confirm(store)
    finally:
        service.close()
    # Simulate a v17 installation before this additive sidecar was
    # introduced. Do not copy v15 rows or add a second positive grant.
    with sqlite3.connect(dbpath) as db:
        for name in (
            "trg_adept_grant_generation_no_delete",
            "trg_adept_grant_generation_no_rewind",
            "trg_adept_grant_generation_insert",
            "trg_adept_grant_generation_update",
            "trg_adept_grant_generation_delete",
        ):
            db.execute(f"DROP TRIGGER IF EXISTS {name}")
        db.execute("DROP TABLE adept_borrower_grant_generations")
    upgraded = Storage(dbpath)
    try:
        assert upgraded.schema_version() == 17
        assert _generation(upgraded) == 1
        _confirm(upgraded)
        assert _generation(upgraded) == 2
        upgraded.close()
        reopened = Storage(dbpath)
        try:
            assert _generation(reopened) == 2
        finally:
            reopened.close()
    finally:
        upgraded.close()


class Hostile:
    def __str__(self):
        raise AssertionError("untrusted coordinate must not be coerced")

    def __eq__(self, _):
        raise AssertionError("untrusted coordinate must not be compared")


class HostileString(str):
    def casefold(self):
        raise AssertionError("subclassed provider must not be normalized")


@pytest.mark.parametrize("bad", [
    None, True, 3, Hostile(), HostileString("alice"),
    "", "bad\nsource", "x" * 1025,
])
@pytest.mark.parametrize("field", [
    "provider_id", "credential_id", "account_id",
    "connection_identity_id", "borrower_identity_id", "profile_id",
])
def test_invalid_grant_provenance_denied_without_sql(
    tmp_path, monkeypatch, bad, field,
):
    store = Storage(tmp_path / "grants.sqlite3")
    try:
        def forbidden(*_a, **_kw):
            pytest.fail("invalid grant provenance reached SQLite")
        monkeypatch.setattr(store, "_connect", forbidden)
        evidence = _evidence()
        evidence[field] = bad
        assert store.adept_borrower_grant_generation(**evidence) is None
    finally:
        store.close()


@pytest.mark.parametrize("bad", [None, 0, True, 1.0, "1", Hostile()])
def test_non_native_version_denied_without_read(tmp_path, monkeypatch, bad):
    store = Storage(tmp_path / "grants.sqlite3")
    try:
        def forbidden(*_a, **_kw):
            pytest.fail("untrusted version reached grant read")
        monkeypatch.setattr(store, "adept_borrower_grant_generation", forbidden)
        assert store.adept_borrower_grant_generation_current(
            **_evidence(), expected_generation=bad,
        ) is False
    finally:
        store.close()
