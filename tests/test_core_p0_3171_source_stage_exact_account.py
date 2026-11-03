"""P0 #3171: stage A requires exactly one currently connected borrower.

The SQLite read is bounded regardless of sibling accounts. The source
reference itself is not delivery or possession proof; no artifact is written.
"""
import pytest

from cifereca.core.canonical_sync_stage import persist_attested_source_references
from cifereca.storage import Storage
from tests.test_core_p0_3171_atomic_publication_v14 import new_source, register
from tests.test_core_p0_3171_canonical_sync_stage import rig


def test_source_reference_stage_exact_account_amid_many_siblings(tmp_path):
    storage = Storage(tmp_path / "state.sqlite")
    try:
        key, snapshot = new_source()
        for n in range(100):
            register(storage, "A", "seller", "card-A", account=f"sibling-{n}")
        with pytest.raises(ValueError, match="canonical_rights_core_binding_changed"):
            persist_attested_source_references(storage, snapshot)
        assert storage.schema_version() == 14
        register(storage, "A", "seller", "card-A", account="buyer-A")
        assert persist_attested_source_references(storage, snapshot) == 1
        assert persist_attested_source_references(storage, snapshot) == 0
        assert storage.provider_scope_account_bound(
            identity_id="A", provider_id="seller",
            credential_id="card-A", account_id="buyer-A",
        ) is True

        register(storage, "A", "another", "other-card", account="borrower")
        with storage._lock, storage._connect() as db:
            other = db.execute(
                "SELECT connection_id FROM provider_connections "
                "WHERE provider_id='another' AND credential_id='other-card'"
            ).fetchone()[0]
            db.execute(
                "UPDATE provider_accounts SET connection_id=? "
                "WHERE identity_id='A' AND provider_id='seller' "
                "AND credential_id='card-A' AND account_id='buyer-A'",
                (other,),
            )
        with pytest.raises(ValueError, match="canonical_rights_core_binding_changed"):
            persist_attested_source_references(storage, snapshot)
        with storage._lock, storage._connect() as db:
            assert db.execute("SELECT COUNT(*) FROM source_references").fetchone() == (1,)
    finally:
        storage.close()


def test_empty_primary_borrower_and_disabled_connection(tmp_path):
    storage = Storage(tmp_path / "state.sqlite")
    try:
        source, snapshot = new_source(provider="paris", account="")
        register(storage, "A", "paris", "card-A", account="")
        assert persist_attested_source_references(storage, snapshot) == 1
        with storage._lock, storage._connect() as db:
            db.execute(
                "UPDATE provider_connections SET enabled=0 "
                "WHERE provider_id='paris' AND credential_id='card-A'"
            )
        with pytest.raises(ValueError, match="canonical_rights_core_binding_changed"):
            persist_attested_source_references(storage, snapshot)
    finally:
        storage.close()


def test_source_reference_stage_rejects_shared_physical_profile_directory(tmp_path):
    """An authenticated A must not write beneath an ambiguous B owner."""
    storage = Storage(tmp_path / "state.sqlite")
    try:
        key, snapshot = new_source()
        register(storage, "A", "seller", "card-A", account="buyer-A")
        # Normally unique by schema; reproduce a corrupted/imported v14 DB.
        with storage._lock, storage._connect() as db:
            db.execute("DROP INDEX uq_identity_directory_name")
        storage.identity_put({
            "identity_id": "shadow", "label": "Conflicting owner",
            "directory_name": "A",
        })
        with pytest.raises(ValueError, match="canonical_rights_core_binding_changed"):
            persist_attested_source_references(storage, snapshot)
        with storage._lock, storage._connect() as db:
            assert db.execute("SELECT COUNT(*) FROM source_references").fetchone() == (0,)
            db.execute("DELETE FROM identities WHERE identity_id='shadow'")
            db.execute(
                "CREATE UNIQUE INDEX uq_identity_directory_name "
                "ON identities(directory_name)"
            )
        # The same source and positive A can be committed after restoring
        # exclusive folder ownership, with no invented B or origin links.
        assert persist_attested_source_references(storage, snapshot) == 1
        with storage._lock, storage._connect() as db:
            assert db.execute("SELECT COUNT(*) FROM origin_links").fetchone() == (0,)
            assert db.execute("SELECT COUNT(*) FROM placements").fetchone() == (0,)
    finally:
        storage.close()


@pytest.mark.parametrize("disabled", ("0", "false", "unexpected"))
def test_source_reference_transaction_rejects_disabled_persisted_source(
    tmp_path, disabled,
):
    """An already authenticated positive A cannot bypass source OFF in SQL."""
    from cifereca.core.source_auto_consent_bridge import _source_id

    storage = Storage(tmp_path / "state.sqlite")
    try:
        source, snapshot = new_source()
        register(storage, "A", "seller", "card-A")
        key = "sync.source.enabled." + _source_id(snapshot.scope)
        storage.metadata_set(key, disabled)
        with pytest.raises(ValueError, match="canonical_rights_core_binding_changed"):
            persist_attested_source_references(storage, snapshot)
        with storage._lock, storage._connect() as db:
            assert db.execute("SELECT COUNT(*) FROM source_references").fetchone() == (0,)
        storage.metadata_set(key, "1")
        assert persist_attested_source_references(storage, snapshot) == 1
        assert persist_attested_source_references(storage, snapshot) == 0
    finally:
        storage.close()


def test_source_disabled_after_private_inventory_before_a_commit_blocks_handoff(
    rig, monkeypatch,
):
    """Only the SQL writer can close the final read-to-commit enablement gap."""
    from cifereca.core import canonical_sync_stage
    from cifereca.core.source_auto_consent_bridge import _source_id
    from cifereca.core.sync_coordination import synchronize_account_source
    from tests.test_core_p0_3171_canonical_sync_stage import refs

    service, storage, _root = rig
    original = canonical_sync_stage.persist_attested_source_references
    key = "sync.source.enabled." + _source_id(service.snapshot.scope)

    def revoke_before_sql(store, snapshot, **kwargs):
        # The private reader has already returned A with its in-memory lease
        # valid. The real Core service reads this durable switch; the fixture
        # intentionally keeps its old cached boolean to test the SQL fence.
        store.metadata_set(key, "0")
        return original(store, snapshot, **kwargs)

    monkeypatch.setattr(
        canonical_sync_stage, "persist_attested_source_references",
        revoke_before_sql,
    )
    result = synchronize_account_source(service, service.account)
    assert service.read_calls == 1
    assert result["status"] == "partial"
    assert result["canonical_rights"]["status"] == "failed"
    assert "canonical_execution" not in result
    assert refs(storage) == ([], 0)
