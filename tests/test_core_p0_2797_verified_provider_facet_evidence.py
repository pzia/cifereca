"""P0 #2797: Library facets require a completed, exact provider origin.

Historical/dubious evidence may be retained in SQLite for audit; it cannot
silently create a supplier/borrower facet even when Placement is present.
No network, files removed or real provider account.
"""
from cifereca.core.library_projection import _verified_provider_origin_coordinates
from cifereca.storage import Storage
from tests.test_core_p0_3171_atomic_publication_v14 import (
    new_source, publish, register, target,
)


def _origins(store, identity="A"):
    return _verified_provider_origin_coordinates(store, identity_id=identity)


def test_library_canonical_facet_requires_provider_observation_evidence(tmp_path):
    store = Storage(tmp_path / "state.sqlite")
    try:
        source, snapshot = new_source()
        register(store, "A", "seller", "card-A")
        path = target(tmp_path, "A")
        published = publish(store, tmp_path / "EBooks", path, source, snapshot)
        expected = (
            published.artifact_id, "A", "seller", "card-A",
            "buyer-A", "edition",
        )
        assert _origins(store) == (expected,)
        assert _origins(store, identity="B") == ()

        with store._lock, store._connect() as db:
            db.execute(
                "UPDATE source_references SET evidence_kind='historical_receipt' "
                "WHERE source_reference_id=?", (published.source_reference_id,),
            )
        assert _origins(store) == ()
        with store._lock, store._connect() as db:
            db.execute(
                "UPDATE source_references SET evidence_kind='provider_response', "
                "evidence_ref='' WHERE source_reference_id=?",
                (published.source_reference_id,),
            )
        assert _origins(store) == ()
        with store._lock, store._connect() as db:
            db.execute(
                "UPDATE source_references SET evidence_ref='positive-right-observation' "
                "WHERE source_reference_id=?", (published.source_reference_id,),
            )
        assert _origins(store) == (expected,)
        assert path.is_file()
    finally:
        store.close()


def test_provider_facet_requires_provider_origin_not_adept_only(tmp_path):
    store = Storage(tmp_path / "state.sqlite")
    try:
        source, snapshot = new_source()
        register(store, "A", "seller", "card-A")
        path = target(tmp_path, "A")
        published = publish(
            store, tmp_path / "EBooks", path, source, snapshot,
            adept_receipt_ref="adept-final-proof",
        )
        assert len(published.origin_link_ids) == 2
        assert len(_origins(store)) == 1
        with store._lock, store._connect() as db:
            db.execute(
                "DELETE FROM origin_links WHERE origin_kind='provider' "
                "AND origin_link_id=?", (published.origin_link_ids[0],),
            )
        assert _origins(store) == ()
        assert path.is_file()
    finally:
        store.close()


def test_corrupt_or_empty_artifact_cannot_create_canonical_supplier_facet(tmp_path):
    """Retain real OriginLink history, but never attach bad Artifact B proof."""
    store = Storage(tmp_path / "state.sqlite")
    try:
        source, snapshot = new_source()
        register(store, "A", "seller", "card-A")
        path = target(tmp_path, "A")
        published = publish(store, tmp_path / "EBooks", path, source, snapshot)
        expected = (published.artifact_id, "A", "seller", "card-A",
                    "buyer-A", "edition")
        assert _origins(store) == (expected,)

        with store._lock, store._connect() as db:
            db.execute(
                "UPDATE artifacts SET size=0 WHERE artifact_id=?",
                (published.artifact_id,),
            )
        assert _origins(store) == ()
        with store._lock, store._connect() as db:
            db.execute(
                "UPDATE artifacts SET size=? WHERE artifact_id=?",
                (path.stat().st_size, published.artifact_id),
            )
        assert _origins(store) == (expected,)

        with store._lock, store._connect() as db:
            # Reproduce an imported/older corrupt DB in this disposable test
            # only: production v14 protects linked Artifact.sha256 by trigger.
            db.execute("DROP TRIGGER trg_artifact_linked_sha_update")
            db.execute(
                "UPDATE artifacts SET sha256=? WHERE artifact_id=?",
                ("a" * 64, published.artifact_id),
            )
        assert _origins(store) == ()
        with store._lock, store._connect() as db:
            assert db.execute(
                "SELECT COUNT(*) FROM origin_links WHERE artifact_id=?",
                (published.artifact_id,),
            ).fetchone() == (1,)
        assert path.is_file()
    finally:
        store.close()
