"""#3171: the recovery journal may trust only an exact connected borrower.

The same two-query bounded guard is used while recording a completed GET
and when deciding whether its staged bytes may be recovered. No network or
ADEPT; 100 unrelated accounts cannot confer authority on one source.
"""
from cifereca.core.canonical_recovery import _exact_borrower_bound_in_db
from cifereca.plugin_api.provider_rights_observation import ProviderRightsScope
from cifereca.storage import Storage
from tests.test_core_p0_3171_atomic_publication_v14 import register


def _bound(storage, scope):
    with storage._lock, storage._connect() as db:
        db.execute("BEGIN")
        return _exact_borrower_bound_in_db(db, scope)


def test_intake_and_recovery_exact_borrower_amid_100_siblings(tmp_path):
    db = Storage(tmp_path / "state.sqlite")
    try:
        for i in range(100):
            register(db, "A", "seller", "card-A", account=f"sibling-{i}")
        scope = ProviderRightsScope("A", "seller", "card-A", "target")
        assert _bound(db, scope) is False
        register(db, "A", "seller", "card-A", account="target")
        assert _bound(db, scope) is True
        assert _bound(db, ProviderRightsScope("A", "seller", "card-A", "sibling-37"))
        assert not _bound(db, ProviderRightsScope("A", "seller", "card-A", "foreign"))
        assert not _bound(db, ProviderRightsScope("B", "seller", "card-A", "target"))

        # A row that only matches the legacy provider/credential/account
        # coordinates but points to another connection is not authoritative.
        register(db, "A", "different", "other", account="side")
        with db._lock, db._connect() as sql:
            other = sql.execute(
                "SELECT connection_id FROM provider_connections "
                "WHERE provider_id='different' AND credential_id='other'"
            ).fetchone()[0]
            original = sql.execute(
                "SELECT connection_id FROM provider_connections "
                "WHERE provider_id='seller' AND credential_id='card-A'"
            ).fetchone()[0]
            sql.execute(
                "UPDATE provider_accounts SET connection_id=? "
                "WHERE provider_id='seller' AND credential_id='card-A' "
                "AND account_id='target'", (other,),
            )
        assert _bound(db, scope) is False
        with db._lock, db._connect() as sql:
            sql.execute(
                "UPDATE provider_accounts SET connection_id=? "
                "WHERE provider_id='seller' AND credential_id='card-A' "
                "AND account_id='target'", (original,),
            )
        assert _bound(db, scope) is True
        with db._lock, db._connect() as sql:
            sql.execute(
                "UPDATE provider_connections SET enabled=0 "
                "WHERE connection_id=?", (original,),
            )
        assert _bound(db, scope) is False
    finally:
        db.close()


def test_primary_empty_borrower_is_not_wildcard(tmp_path):
    db = Storage(tmp_path / "state.sqlite")
    try:
        register(db, "A", "paris", "card-A", account="")
        assert _bound(db, ProviderRightsScope("A", "paris", "card-A", "")) is True
        assert _bound(db, ProviderRightsScope("A", "paris", "card-A", "other")) is False
        assert _bound(db, ProviderRightsScope("B", "paris", "card-A", "")) is False
    finally:
        db.close()
