"""P0 #5975: v17-compatible persistent source epoch refuses A -> B -> A.

This tests actual SQLite triggers across direct SQL, legacy writers, restarts
and multi-provider scopes. The token is a veto only, never an ADEPT grant.
"""
from __future__ import annotations

import sqlite3

import pytest

from cifereca.storage import CURRENT_SCHEMA_VERSION, Storage


def put(store, provider="synthetic-pnb", credential="card-a", owner="alice",
        enabled=True):
    store.provider_connection_put({
        "provider_id": provider, "credential_id": credential,
        "identity_id": owner, "enabled": enabled,
    })


def epoch(store, *, provider="synthetic-pnb", credential="card-a",
          owner="alice"):
    return store.provider_connection_epoch(provider, credential, owner)


def test_generation_is_v17_rollback_compatible_but_persistent(tmp_path):
    store = Storage(tmp_path / "source.sqlite3")
    try:
        assert CURRENT_SCHEMA_VERSION == store.schema_version() == 17
        assert epoch(store) is None
        put(store)
        original = epoch(store)
        assert type(original) is int and original > 0
        assert store.provider_connection_epoch_current(
            "synthetic-pnb", "card-a", "alice", original,
        ) is True
        store.close()
        reopened = Storage(tmp_path / "source.sqlite3")
        try:
            assert reopened.schema_version() == 17
            assert epoch(reopened) == original
        finally:
            reopened.close()
    finally:
        store.close()


@pytest.mark.parametrize("path", ["owner_aba", "disable_restore", "delete_recreate",
                                   "credential_rekey", "raw_sql_owner_aba"])
def test_source_epoch_never_recovers_after_mutating_connection(tmp_path, path):
    store = Storage(tmp_path / "source.sqlite3")
    try:
        put(store)
        old = epoch(store)
        assert old is not None
        if path == "owner_aba":
            put(store, owner="bob")
            assert epoch(store) is None
            put(store, owner="alice")
        elif path == "disable_restore":
            put(store, enabled=False)
            assert epoch(store) is None
            put(store, enabled=True)
        elif path == "delete_recreate":
            with store._lock, store._connect() as db:
                db.execute("DELETE FROM provider_connections "
                           "WHERE provider_id=? AND credential_id=?",
                           ("synthetic-pnb", "card-a"))
            assert epoch(store) is None
            put(store)
        elif path == "credential_rekey":
            with store._lock, store._connect() as db:
                db.execute(
                    "UPDATE provider_connections SET credential_id=? "
                    "WHERE provider_id=? AND credential_id=?",
                    ("new-card", "synthetic-pnb", "card-a"),
                )
            assert epoch(store) is None
            with store._lock, store._connect() as db:
                db.execute(
                    "UPDATE provider_connections SET credential_id=? "
                    "WHERE provider_id=? AND credential_id=?",
                    ("card-a", "synthetic-pnb", "new-card"),
                )
        else:
            with store._lock, store._connect() as db:
                db.execute(
                    "UPDATE provider_connections SET identity_id='bob' "
                    "WHERE provider_id='synthetic-pnb' AND credential_id='card-a'"
                )
                db.execute(
                    "UPDATE provider_connections SET identity_id='alice' "
                    "WHERE provider_id='synthetic-pnb' AND credential_id='card-a'"
                )
        current = epoch(store)
        assert type(current) is int and current > old
        assert not store.provider_connection_epoch_current(
            "synthetic-pnb", "card-a", "alice", old,
        )
        assert store.provider_connection_epoch_current(
            "synthetic-pnb", "card-a", "alice", current,
        )
        store.close()
        restarted = Storage(tmp_path / "source.sqlite3")
        try:
            assert epoch(restarted) == current
            assert not restarted.provider_connection_epoch_current(
                "synthetic-pnb", "card-a", "alice", old,
            )
        finally:
            restarted.close()
    finally:
        store.close()


def test_unrelated_provider_and_credential_never_revoke_other_token(tmp_path):
    store = Storage(tmp_path / "source.sqlite3")
    try:
        put(store)
        put(store, provider="other-library")
        put(store, credential="card-b")
        other_provider = epoch(store, provider="other-library")
        other_credential = epoch(store, credential="card-b")
        original = epoch(store)
        put(store, owner="bob")
        put(store, owner="alice")
        assert epoch(store) > original
        assert epoch(store, provider="other-library") == other_provider
        assert epoch(store, credential="card-b") == other_credential
    finally:
        store.close()


def test_direct_epoch_row_rewind_and_tombstone_deletion_are_forbidden(tmp_path):
    store = Storage(tmp_path / "source.sqlite3")
    try:
        put(store)
        original = epoch(store)
        with pytest.raises(sqlite3.IntegrityError, match="source_epoch_rewind_forbidden"):
            with store._lock, store._connect() as db:
                db.execute(
                    "UPDATE provider_connection_epochs SET epoch=epoch-1 "
                    "WHERE provider_id='synthetic-pnb' AND credential_id='card-a'"
                )
        with pytest.raises(sqlite3.IntegrityError, match="source_epoch_retirement_forbidden"):
            with store._lock, store._connect() as db:
                db.execute(
                    "DELETE FROM provider_connection_epochs "
                    "WHERE provider_id='synthetic-pnb' AND credential_id='card-a'"
                )
        assert epoch(store) == original
    finally:
        store.close()


class Hostile:
    def __str__(self):
        raise AssertionError("must not coerce untrusted source")

    def __eq__(self, other):
        raise AssertionError("must not compare untrusted source")


class HostileString(str):
    def casefold(self):
        raise AssertionError("must not normalize subclassed source")


@pytest.mark.parametrize("bad", [None, 0, True, Hostile(), HostileString("alice"),
                                  "bad\nsource", "", "x" * 1025])
@pytest.mark.parametrize("field", ["provider", "credential", "identity"])
def test_invalid_scope_refuses_without_reading_sql(tmp_path, monkeypatch, bad, field):
    store = Storage(tmp_path / "source.sqlite3")
    try:
        def forbid(*_args, **_kwargs):
            pytest.fail("invalid source must not query SQLite")
        monkeypatch.setattr(store, "_connect", forbid)
        values = ["synthetic-pnb", "card-a", "alice"]
        values[{"provider": 0, "credential": 1, "identity": 2}[field]] = bad
        assert store.provider_connection_epoch(*values) is None
    finally:
        store.close()


@pytest.mark.parametrize("bad", [None, False, 0, 1.0, "1", Hostile(), True])
def test_non_native_epoch_is_never_a_valid_witness(tmp_path, monkeypatch, bad):
    store = Storage(tmp_path / "source.sqlite3")
    try:
        def forbid(*_args, **_kwargs):
            pytest.fail("invalid epoch must not reach SQLite")
        monkeypatch.setattr(store, "provider_connection_epoch", forbid)
        assert store.provider_connection_epoch_current(
            "synthetic-pnb", "card-a", "alice", bad,
        ) is False
    finally:
        store.close()


def test_existing_v17_rows_seed_once_and_never_reset_generation(tmp_path):
    dbpath = tmp_path / "source.sqlite3"
    first = Storage(dbpath)
    put(first)
    first.close()
    # Simulate a v17 installation before the sidecar existed. The schema
    # revision remains 17: reboot must add the table without rebuilding any
    # source row or using one as a positive entitlement/grant.
    with sqlite3.connect(dbpath) as db:
        for name in (
            "trg_provider_epoch_insert", "trg_provider_epoch_update",
            "trg_provider_epoch_delete", "trg_provider_epoch_no_delete",
            "trg_provider_epoch_no_rewind",
        ):
            db.execute(f"DROP TRIGGER IF EXISTS {name}")
        db.execute("DROP TABLE IF EXISTS provider_connection_epochs")
    migrated = Storage(dbpath)
    try:
        seeded = epoch(migrated)
        assert seeded == 1
        put(migrated, owner="bob")
        put(migrated, owner="alice")
        assert epoch(migrated) > seeded
        migrated.close()
        reloaded = Storage(dbpath)
        try:
            assert epoch(reloaded) > seeded
        finally:
            reloaded.close()
    finally:
        migrated.close()


def test_epoch_does_not_grant_identity_loan_or_profile(tmp_path):
    store = Storage(tmp_path / "source.sqlite3")
    try:
        put(store)
        current = epoch(store)
        assert current is not None
        # The new witness does not persist or infer any ADEPT grant,
        # acquisition, or right.
        assert store.adept_borrower_grant_get(
            provider_id="synthetic-pnb", credential_id="card-a",
            account_id="primary",
        ) is None
        assert store.acquisitions(provider_id="synthetic-pnb") == []
        assert store.entitlements(provider_id="synthetic-pnb") == []
    finally:
        store.close()
