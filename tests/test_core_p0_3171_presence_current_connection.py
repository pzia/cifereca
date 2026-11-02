"""#3171: an OriginLink is B evidence only for its *current* Core connection.

A stale borrower row pointing to another provider connection cannot attest
previous physical bytes. The bytes and provenance remain untouched.
"""
from cifereca.core.convergence_verified_presence import plan_existing_verified_presence
from cifereca.storage import Storage
from tests.test_core_p0_3171_atomic_publication_v14 import (
    new_source, publish, register, target,
)


def _satisfied(store, snapshot, root):
    result = plan_existing_verified_presence(
        store, snapshot, library_root=root,
    )
    assert len(result.decisions) == 1
    return result.decisions[0].provider_satisfied


def test_foreign_connection_id_revokes_presence_without_erasing_history(tmp_path):
    store = Storage(tmp_path / "state.sqlite")
    try:
        source, snapshot = new_source()
        for index in range(100):
            register(store, "A", "seller", "card-A", account=f"sibling-{index}")
        register(store, "A", "seller", "card-A", account="buyer-A")
        file = target(tmp_path, "A")
        root = tmp_path / "EBooks"
        published = publish(store, root, file, source, snapshot)
        assert _satisfied(store, snapshot, root) is True
        register(store, "A", "unrelated", "card-B", account="buyer-B")
        with store._lock, store._connect() as db:
            foreign_connection = db.execute(
                "SELECT connection_id FROM provider_connections "
                "WHERE provider_id='unrelated' AND credential_id='card-B'"
            ).fetchone()[0]
            original_connection = db.execute(
                "SELECT connection_id FROM provider_connections "
                "WHERE provider_id='seller' AND credential_id='card-A'"
            ).fetchone()[0]
            db.execute(
                "UPDATE provider_accounts SET connection_id=? "
                "WHERE identity_id='A' AND provider_id='seller' "
                "AND credential_id='card-A' AND account_id='buyer-A'",
                (foreign_connection,),
            )
        assert _satisfied(store, snapshot, root) is False
        with store._lock, store._connect() as db:
            assert db.execute(
                "SELECT COUNT(*) FROM origin_links WHERE artifact_id=?",
                (published.artifact_id,),
            ).fetchone() == (1,)
        assert file.is_file()
        with store._lock, store._connect() as db:
            db.execute(
                "UPDATE provider_accounts SET connection_id=? "
                "WHERE identity_id='A' AND provider_id='seller' "
                "AND credential_id='card-A' AND account_id='buyer-A'",
                (original_connection,),
            )
        assert _satisfied(store, snapshot, root) is True
    finally:
        store.close()
