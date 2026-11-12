"""#5743: provider-neutral transaction guard before historical SQLite index cutover."""
from concurrent.futures import ThreadPoolExecutor
import sqlite3

import pytest

from cifereca.storage import Storage


def connection(provider, credential, identity="alice"):
    return {
        "provider_id": provider,
        "credential_id": credential,
        "identity_id": identity,
    }


def test_synthetic_limit_one_refuses_second_credential_without_disclosure(tmp_path):
    storage = Storage(tmp_path / "limits.sqlite3")
    storage.provider_connection_put(
        connection("synthetic", "secret-first"), max_accounts_per_identity=1
    )
    with pytest.raises(ValueError, match="provider_account_limit_exceeded") as error:
        storage.provider_connection_put(
            connection("synthetic", "secret-second"), max_accounts_per_identity=1
        )
    assert "secret" not in str(error.value)
    assert len(storage.provider_connections()) == 1
    # Idempotent replay of the exact credential is permitted.
    storage.provider_connection_put(
        connection("synthetic", "secret-first"), max_accounts_per_identity=1
    )
    assert len(storage.provider_connections()) == 1
    storage.close()


def test_synthetic_two_per_identity_and_isolated_identity_scopes(tmp_path):
    storage = Storage(tmp_path / "limits.sqlite3")
    for credential in ("first", "second"):
        storage.provider_connection_put(
            connection("synthetic", credential), max_accounts_per_identity=2
        )
    with pytest.raises(ValueError, match="provider_account_limit_exceeded"):
        storage.provider_connection_put(
            connection("synthetic", "third"), max_accounts_per_identity=2
        )
    storage.provider_connection_put(
        connection("synthetic", "other-identity", "bob"),
        max_accounts_per_identity=2,
    )
    storage.provider_connection_put(
        connection("unbounded", "first"), max_accounts_per_identity=None,
    )
    storage.provider_connection_put(
        connection("unbounded", "second"), max_accounts_per_identity=None,
    )
    assert len(storage.provider_connections()) == 5
    storage.close()


@pytest.mark.parametrize("limit", [0, -1, False, "1", 1.0])
def test_invalid_limit_refused_before_write(tmp_path, limit):
    storage = Storage(tmp_path / "limits.sqlite3")
    with pytest.raises(ValueError, match="invalid_provider_account_limit"):
        storage.provider_connection_put(
            connection("synthetic", "secret"), max_accounts_per_identity=limit
        )
    assert storage.provider_connections() == []
    storage.close()


def test_two_independent_storage_writers_cannot_bypass_limit(tmp_path):
    path = tmp_path / "concurrent.sqlite3"
    a, b = Storage(path), Storage(path)
    def attempt(args):
        storage, credential = args
        try:
            storage.provider_connection_put(
                connection("synthetic", credential), max_accounts_per_identity=1
            )
            return "accepted"
        except ValueError:
            return "refused"
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(attempt, ((a, "private-a"), (b, "private-b"))))
    assert sorted(outcomes) == ["accepted", "refused"]
    assert len(a.provider_connections()) == 1
    a.close()
    b.close()


def test_historic_kobo_indexes_remain_effective_without_supplier_branch(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    storage = Storage(path)
    with sqlite3.connect(path) as db:
        indexes = {row[1] for row in db.execute("PRAGMA index_list('provider_connections')")}
        assert "uq_kobo_connection_identity" in indexes
    first = storage.provider_connection_put(connection("kobo", "private-one"))
    with pytest.raises(ValueError) as error:
        storage.provider_connection_put(connection("kobo", "private-two"))
    assert "private" not in str(error.value)
    assert len(storage.provider_connections()) == 1
    storage.provider_account_put({
        "connection_id": first, "provider_id": "kobo",
        "credential_id": "private-one", "identity_id": "alice",
        "account_id": "book-one",
    })
    with pytest.raises(ValueError) as error:
        storage.provider_account_put({
            "connection_id": first, "provider_id": "kobo",
            "credential_id": "private-one", "identity_id": "alice",
            "account_id": "book-two",
        })
    assert "private" not in str(error.value)
    assert len(storage.provider_accounts()) == 1
    storage.close()
