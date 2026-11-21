"""#5225: only exact origin and fresh local bytes suppress an admitted GET.

Real v17 SQLite, canonical publication and physical bytes. The preparation
port is exercised without granting or triggering actual provider actions.
"""
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from cifereca.core.loan_local_preparation import prepare
from cifereca.core.loan_verified_presence import canonical_loan_presence
from cifereca.services import decide_artifact_reuse, blocked_automatic_adept_retry
from tests.test_core_p0_3171_atomic_publication_v14 import (
    store, register, new_source, publish, target, rows, BYTES,
)


def _loan():
    return NS(id="cycle-1", record_id="edition", title="Synthetic",
              source_provider="seller", source_credential="card-A",
              source_account_id="buyer-A")


def _prepare(service, loan, *, force=False):
    return prepare(
        service, loan, identity=NS(identity_id="A"), entitlement_id=None,
        force_refresh=force, started=0, inspect_artifact=service.inspector,
        decide_artifact_reuse=decide_artifact_reuse,
        blocked_automatic_adept_retry=blocked_automatic_adept_retry,
        time=NS(monotonic=lambda: 1),
        _TRACE_LOG=NS(info=lambda *a: None, warning=lambda *a: None),
    )


@pytest.mark.parametrize("case", [
    "exact", "catalog_only", "other_cycle", "purchase", "other_account",
    "missing_origin", "changed_bytes", "protected", "symlink", "two_branches",
])
def test_prepare_requires_exact_current_canonical_loan_delivery(store, tmp_path, case):
    root = tmp_path / "EBooks"
    register(store, "A", "seller", "card-A")
    path = target(tmp_path, "A", "Acquisitions/book.pdf")
    loan = _loan()
    inspector = Mock(return_value=NS(valid=True, protected=case == "protected"))
    service = NS(storage=store, library_dir=lambda: root, inspector=inspector,
                 catalog_index_path=Mock())
    if case == "other_account":
        register(store, "A", "seller", "card-A", account="other-borrower")
    if case == "catalog_only":
        from tests.test_core_p0_3171_atomic_publication_v14 import SHA
        artifact = "sha256:" + SHA
        store.artifact_put({"artifact_id": artifact, "sha256": SHA,
                            "size": len(BYTES), "media_format": "pdf"})
        pid = store.placement_put({"artifact_id": artifact, "identity_id": "A",
                                   "path": str(path), "state": "present"})
    else:
        source, snapshot = new_source(
            "A", branch="loans", edition=loan.record_id,
            account="other-borrower" if case == "other_account" else "buyer-A",
            right="cycle-2" if case == "other_cycle" else loan.id,
            kind="purchase" if case == "purchase" else "loan",
        )
        result = publish(store, root, path, source, snapshot)
        artifact, pid = result.artifact_id, result.placement_id
        if case == "missing_origin":
            with store._lock, store._connect() as db:
                db.execute("DELETE FROM origin_links WHERE source_reference_id=?",
                           (result.source_reference_id,))
        elif case == "two_branches":
            other, observed = new_source("A", branch="other", edition=loan.record_id,
                                         right=loan.id, kind="loan")
            publish(store, root, path, other, observed)
        elif case == "changed_bytes":
            path.write_bytes(BYTES + b"!")
        elif case == "symlink":
            saved = path.with_name("different.pdf")
            path.rename(saved)
            path.symlink_to(saved)

    # A historically valid local catalogue row is not source-specific proof.
    store.catalog_entry_put({
        "artifact_id": artifact, "placement_id": pid, "identity_id": "A",
        "provider_id": "seller", "item_id": loan.record_id,
        "title": loan.title, "local_state": "local", "metadata": {"path": str(path)},
    })
    before = rows(store)
    result = _prepare(service, loan)
    if case == "exact":
        assert result.outcome["status"] == "unchanged"
        assert result.decision.action == "reuse"
        assert _prepare(service, loan, force=True).decision.action == "reacquire_explicit"
    else:
        assert result.outcome is None and result.decision.action == "acquire"
    assert rows(store) == before
    assert store.entitlements() == [] and store.acquisitions() == []
    service.catalog_index_path.assert_not_called()
    assert path.read_bytes() == (BYTES + b"!" if case == "changed_bytes" else BYTES)


def test_legacy_receipt_other_known_cycle_is_not_reused(store, tmp_path):
    root = tmp_path / "EBooks"
    register(store, "A", "seller", "card-A")
    path = target(tmp_path, "A")
    loan = _loan()
    store.acquisition_put({
        "identity_id": "A", "provider_id": "seller", "credential_id": "card-A",
        "account_id": "buyer-A", "item_id": "edition", "title": loan.title,
        "status": "complete", "strategy": "loan_auto", "executor": "provider",
        "metadata": {"path": str(path), "provider_loan_id": "cycle-2"},
    })
    service = NS(storage=store, library_dir=lambda: root,
                 inspector=Mock(return_value=NS(valid=True, protected=False)),
                 catalog_index_path=Mock())
    before = store.acquisitions()
    result = _prepare(service, loan)
    assert result.outcome is None and result.decision.action == "acquire"
    assert store.acquisitions() == before and path.read_bytes() == BYTES
    service.catalog_index_path.assert_not_called()


def test_nested_presence_read_cannot_commit_or_rollback_caller(store, tmp_path):
    register(store, "A", "seller", "card-A")
    root = tmp_path / "EBooks"
    path = target(tmp_path, "A")
    key, snapshot = new_source("A", branch="loans", right="cycle-1", kind="loan")
    publish(store, root, path, key, snapshot)
    service = NS(storage=store, library_dir=lambda: root)
    inspector = Mock(return_value=NS(valid=True, protected=False))
    db = store._connect()
    try:
        db.execute("BEGIN IMMEDIATE")
        db.execute("INSERT INTO metadata(key,value) VALUES(?,?)", ("synthetic.5225", "pending"))
        assert canonical_loan_presence(service, _loan(), "A", inspector=inspector) is False
        assert db.in_transaction
        assert db.execute("SELECT value FROM metadata WHERE key='synthetic.5225'").fetchone() == ("pending",)
        inspector.assert_not_called()
        db.rollback()
        assert canonical_loan_presence(service, _loan(), "A", inspector=inspector) is True
        assert store.metadata_get("synthetic.5225") is None
        assert path.read_bytes() == BYTES
    finally:
        if db.in_transaction:
            db.rollback()


@pytest.mark.parametrize("protection", [None, 0, "false", "unknown"])
def test_unknown_protection_is_not_openable(store, tmp_path, protection):
    register(store, "A", "seller", "card-A")
    root = tmp_path / "EBooks"
    path = target(tmp_path, "A")
    key, snapshot = new_source("A", branch="loans", right="cycle-1", kind="loan")
    publish(store, root, path, key, snapshot)
    service = NS(storage=store, library_dir=lambda: root)
    inspector = Mock(return_value=NS(valid=True, protected=protection))
    before = rows(store)
    assert canonical_loan_presence(service, _loan(), "A", inspector=inspector) is False
    assert rows(store) == before and path.read_bytes() == BYTES


@pytest.mark.parametrize("changed", [
    "root", "storage", "id", "record_id", "source_provider",
    "source_credential", "source_account_id", "identity_id",
])
def test_inspection_cannot_transfer_presence_to_changed_scope(store, tmp_path, changed):
    register(store, "A", "seller", "card-A")
    root = tmp_path / "EBooks"
    path = target(tmp_path, "A")
    key, snapshot = new_source("A", branch="loans", right="cycle-1", kind="loan")
    publish(store, root, path, key, snapshot)
    loan = _loan()
    loan.identity_id = "A"
    service = NS(storage=store, library_dir=lambda: root)
    before = rows(store)

    def inspect(_path):
        assert _path == path
        if changed == "root":
            service.library_dir = lambda: tmp_path / "another-root"
        elif changed == "storage":
            service.storage = object()
        else:
            setattr(loan, changed, "foreign")
        return NS(valid=True, protected=False)

    assert canonical_loan_presence(service, loan, "A", inspector=inspect) is False
    assert rows(store) == before and path.read_bytes() == BYTES
