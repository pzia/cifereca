"""P0 #3171: exact owner and borrower admission without roster enumeration.

No supplier API, DRM or real profile. A SQLite rebind or ambiguous account
never authorizes a selected offer/GET, including for the primary '' borrower.
"""
from cifereca.storage import Storage
from tests.test_core_p0_3171_atomic_publication_v14 import register


def _bound(db, *, profile="A", provider="seller", credential="card-A", account="buyer-A"):
    return db.provider_scope_account_bound(
        identity_id=profile, provider_id=provider,
        credential_id=credential, account_id=account,
    )


def test_exact_connected_borrower_amid_many_accounts_and_rebind(tmp_path):
    db = Storage(tmp_path / "state.sqlite")
    try:
        for i in range(100):
            register(db, "A", "seller", "card-A", account=f"sibling-{i}")
        assert _bound(db) is False
        register(db, "A", "seller", "card-A", account="buyer-A")
        assert _bound(db) is True
        assert _bound(db, account="sibling-71") is True
        assert _bound(db, account="foreign") is False
        assert _bound(db, credential="card-B") is False
        assert _bound(db, profile="B") is False

        with db._lock, db._connect() as sql:
            sql.execute(
                "UPDATE provider_accounts SET account_id='replaced' "
                "WHERE provider_id='seller' AND credential_id='card-A' "
                "AND account_id='buyer-A'"
            )
        assert _bound(db) is False
        assert _bound(db, account="replaced") is True

        with db._lock, db._connect() as sql:
            sql.execute(
                "UPDATE provider_connections SET enabled=0 "
                "WHERE provider_id='seller' AND credential_id='card-A'"
            )
        assert _bound(db, account="replaced") is False
        with db._lock, db._connect() as sql:
            sql.execute(
                "UPDATE provider_connections SET enabled=1,identity_id='B' "
                "WHERE provider_id='seller' AND credential_id='card-A'"
            )
        assert _bound(db, account="replaced") is False
    finally:
        db.close()


def test_primary_empty_borrower_is_exact_not_a_wildcard(tmp_path):
    db = Storage(tmp_path / "state.sqlite")
    try:
        register(db, "A", "paris", "card-A", account="")
        assert _bound(db, provider="paris", account="") is True
        assert _bound(db, provider="paris", account="other") is False
        assert _bound(db, provider="paris", account="paris:card-A:primary") is False
        for invalid in (None, 1, [], "bad\\naccount", "bad\\x00account"):
            assert db.provider_scope_account_bound(
                identity_id="A", provider_id="paris",
                credential_id="card-A", account_id=invalid,
            ) is False
    finally:
        db.close()
