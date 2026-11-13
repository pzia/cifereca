from hashlib import sha256

import pytest

from cifereca.core.canonical_publication import publish_completed_acquisition
from cifereca.core.library_audit import audit_library
from tests.test_core_p0_3171_atomic_publication_v14 import new_source, register, store
from tests.test_library_reconciliation import epub


@pytest.mark.parametrize("change, confirmed", [
    ("none", True),
    ("missing_origin", False),
    ("other_account", False),
    ("other_identity", False),
    ("unverified", False),
    ("inactive", False),
    ("changed_bytes", False),
    ("duplicate_placement", False),
])
def test_audit_requires_exact_persisted_purchase_origin(store, tmp_path, change, confirmed):
    register(store, "A", "synthetic-books", "card-A")
    source, snapshot = new_source(provider="synthetic-books")
    root = tmp_path / "library"
    path = root / "A" / "book.epub"
    path.parent.mkdir(parents=True)
    epub(path, title="Attested purchase")
    result = publish_completed_acquisition(
        store, library_root=root, path=path, source=source, snapshot=snapshot,
        final_sha256=sha256(path.read_bytes()).hexdigest(),
        delivery_receipt_ref="completed-delivery",
    )
    mutations = {
        "missing_origin": "DELETE FROM origin_links",
        "other_account": "UPDATE source_references SET account_id='other'",
        "other_identity": "UPDATE source_references SET identity_id='B'",
        "unverified": "UPDATE source_references SET evidence_kind='legacy_unverified'",
        "inactive": "UPDATE source_references SET state='inactive'",
    }
    if change in mutations:
        with store._lock, store._connect() as db:
            # Simulate an inconsistent imported database for owner mutations.
            db.execute("DROP TRIGGER trg_linked_source_coords_immutable")
            db.execute(mutations[change])
    elif change == "changed_bytes":
        epub(path, title="Different bytes")
    elif change == "duplicate_placement":
        duplicate = path.with_name("duplicate.epub")
        duplicate.write_bytes(path.read_bytes())
        store.placement_put({
            "identity_id": "A", "artifact_id": result.artifact_id,
            "path": duplicate, "state": "present",
        })

    audit = audit_library(store.path, {"A": (path.parent,)})
    assert not audit.reasons
    assert not audit.snapshot.reasons
    assert audit.snapshot.tables["entitlements"] == []
    assert audit.snapshot.tables["acquisition_ledger"] == []
    edges = [e for e in audit.reconciliation.edges if e.certainty == "attested"]
    assert bool(edges) is confirmed
    assert ("confirmed_link" in audit.reconciliation.files[0]) is confirmed
    if confirmed:
        assert [e.table for e in edges] == ["provider_origins"]
        assert audit.groups.counts[(0, "synthetic-books")]["confirmed_link"] == 1
