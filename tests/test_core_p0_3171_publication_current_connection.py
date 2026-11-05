"""P0 #3171: the final canonical SQL commit binds borrower to live connection.

A historical provider_accounts row whose textual coordinates still match but
whose connection_id points elsewhere must not publish Artifact/OriginLink or
satisfy source B. The same correction must work with 100 sibling borrowers.
"""
from __future__ import annotations

import pytest

from cifereca.storage import Storage
from tests.test_core_p0_3171_atomic_publication_v14 import (
    new_source, publish, register, rows, target,
)


def test_final_publication_refuses_historical_foreign_connection_and_restores(tmp_path):
    store = Storage(tmp_path / "state.sqlite")
    try:
        source, snapshot = new_source()
        for i in range(100):
            register(store, "A", "seller", "card-A", account=f"sibling-{i}")
        register(store, "A", "seller", "card-A", account="buyer-A")
        register(store, "A", "other-supplier", "other-card", account="someone")
        path = target(tmp_path, "A")
        root = tmp_path / "EBooks"
        before = path.read_bytes()

        with store._lock, store._connect() as db:
            valid = db.execute(
                "SELECT connection_id FROM provider_connections "
                "WHERE provider_id='seller' AND credential_id='card-A'"
            ).fetchone()[0]
            foreign = db.execute(
                "SELECT connection_id FROM provider_connections "
                "WHERE provider_id='other-supplier' AND credential_id='other-card'"
            ).fetchone()[0]
            db.execute(
                "UPDATE provider_accounts SET connection_id=? "
                "WHERE identity_id='A' AND provider_id='seller' "
                "AND credential_id='card-A' AND account_id='buyer-A'",
                (foreign,),
            )
        assert not store.provider_scope_account_bound(
            identity_id="A", provider_id="seller",
            credential_id="card-A", account_id="buyer-A",
        )
        with pytest.raises(ValueError, match="canonical_account_not_registered"):
            publish(store, root, path, source, snapshot)
        assert rows(store) == (0, 0, 0, 0)
        assert path.read_bytes() == before

        with store._lock, store._connect() as db:
            db.execute(
                "UPDATE provider_accounts SET connection_id=? "
                "WHERE identity_id='A' AND provider_id='seller' "
                "AND credential_id='card-A' AND account_id='buyer-A'",
                (valid,),
            )
        result = publish(store, root, path, source, snapshot)
        assert result.changed is True
        assert rows(store) == (1, 1, 1, 1)
        assert publish(store, root, path, source, snapshot).changed is False
        assert path.read_bytes() == before
    finally:
        store.close()


def test_final_publication_requires_enabled_connection_not_just_account(tmp_path):
    store = Storage(tmp_path / "state.sqlite")
    try:
        source, snapshot = new_source()
        register(store, "A", "seller", "card-A")
        path = target(tmp_path, "A")
        with store._lock, store._connect() as db:
            db.execute(
                "UPDATE provider_connections SET enabled=0 "
                "WHERE provider_id='seller' AND credential_id='card-A'"
            )
        with pytest.raises(ValueError, match="canonical_connection_or_profile_rebound"):
            publish(store, tmp_path / "EBooks", path, source, snapshot)
        assert rows(store) == (0, 0, 0, 0)
        assert path.exists()
    finally:
        store.close()
