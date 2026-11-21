"""#5225: the common completed-delivery journal is revalidated on recovery."""
import json
import sqlite3

import pytest

from cifereca.core import canonical_recovery as recovery
from cifereca.core import convergence_execution as execution
from cifereca.locking import InterProcessLock
from tests.test_core_p0_3171_atomic_publication_v14 import rows, SHA, BYTES
from tests.test_core_p0_3171_convergence_execution import rig, run, fake_delivery
from tests.test_core_p0_3171_crash_recovery_v14 import journal, force_failure_once


def _staged(rig, monkeypatch):
    service, account, source, snapshot, root, lock_path = rig
    original = force_failure_once(monkeypatch)
    assert run(service, account, snapshot, root, lock_path, {source},
               fake_delivery(service, root))[0].status == "failed"
    monkeypatch.setattr(execution, "publish_completed_acquisition", original)
    assert service.calls == 1
    assert journal(service.storage)[0][:2] == ("canonical_receipt_staged", None)
    return snapshot.rights[0].delivery_offer_id


@pytest.mark.parametrize("change", ["receipt", "borrower", "deleted"])
def test_concurrent_checkpoint_change_blocks_this_attempt(rig, monkeypatch, change):
    service, account, source, snapshot, root, lock_path = rig
    _staged(rig, monkeypatch)
    original = recovery._path_verified
    other = sqlite3.connect(service.storage.path)
    try:
        def mutate(*args, **kwargs):
            actual = original(*args, **kwargs)
            with other:
                if change == "receipt":
                    raw, = other.execute(
                        "SELECT metadata_json FROM acquisition_ledger WHERE acquisition_id LIKE 'canonical-intake:%'"
                    ).fetchone()
                    data = json.loads(raw)
                    data["receipt"] = "changed-receipt"
                    other.execute("UPDATE acquisition_ledger SET metadata_json=? WHERE acquisition_id LIKE 'canonical-intake:%'",
                                  (json.dumps(data),))
                elif change == "borrower":
                    other.execute("DELETE FROM provider_accounts WHERE provider_id='seller-a'")
                else:
                    other.execute("DELETE FROM acquisition_ledger WHERE acquisition_id LIKE 'canonical-intake:%'")
            return actual
        monkeypatch.setattr(recovery, "_path_verified", mutate)
        result, = run(service, account, snapshot, root, lock_path, {source},
                      lambda *_: pytest.fail("racing receipt cannot authorize a second GET"))
        assert result.status == "failed" and result.reason == "canonical_recovery_failed"
        assert rows(service.storage) == (0, 0, 0, 0)
        assert service.calls == 1
        assert (root / "A" / "book.pdf").read_bytes() == BYTES
    finally:
        other.close()


@pytest.mark.parametrize("operation", ["record", "recover", "mark"])
def test_caller_transaction_is_not_committed_or_rolled_back(rig, monkeypatch, operation):
    service, _, source, _, root, lock_path = rig
    offer = _staged(rig, monkeypatch)
    storage = service.storage
    db = storage._connect()
    observer = sqlite3.connect(storage.path)
    before = journal(storage)
    try:
        with InterProcessLock(lock_path) as lock:
            options = dict(source=source, offer_id=offer, shared_lock=lock)
            completed = recovery.recover_completed_delivery(storage, library_root=root, **options)
            assert completed is not None
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT INTO metadata(key,value) VALUES('synthetic-private-write','pending')")
            with pytest.raises(ValueError, match="canonical_intake_nested_transaction"):
                if operation == "recover":
                    recovery.recover_completed_delivery(storage, library_root=root, **options)
                elif operation == "record":
                    recovery.record_completed_delivery_for_recovery(
                        storage, library_root=root, completed=completed, **options)
                else:
                    recovery.mark_completed_delivery_published(storage, artifact_id="sha256:" + SHA, **options)
            assert db.in_transaction
            assert db.execute("SELECT value FROM metadata WHERE key='synthetic-private-write'").fetchone() == ("pending",)
            assert observer.execute("SELECT value FROM metadata WHERE key='synthetic-private-write'").fetchone() is None
            db.rollback()
        assert journal(storage) == before
    finally:
        if db.in_transaction:
            db.rollback()
        observer.close()


def test_wrong_final_digest_cannot_advance_operation_journal(rig, monkeypatch):
    service, _, source, _, root, lock_path = rig
    offer = _staged(rig, monkeypatch)
    before = journal(service.storage)
    with InterProcessLock(lock_path) as lock:
        with pytest.raises(ValueError, match="canonical_intake_checkpoint_conflict"):
            recovery.mark_completed_delivery_published(
                service.storage, source=source, offer_id=offer,
                artifact_id="sha256:" + "0"*64, shared_lock=lock)
    assert journal(service.storage) == before
    assert (root / "A" / "book.pdf").read_bytes() == BYTES


def test_completed_handoff_and_final_B_commit_use_full_durability(rig, monkeypatch):
    service, account, source, snapshot, root, lock_path = rig
    storage = service.storage
    db = storage._connect()
    db.execute("PRAGMA synchronous=NORMAL")
    seen = []
    db.set_trace_callback(seen.append)
    try:
        original = force_failure_once(monkeypatch)
        _staged(rig, monkeypatch)
        monkeypatch.setattr(execution, "publish_completed_acquisition", original)
        assert db.execute("PRAGMA synchronous").fetchone() == (1,)
        assert run(service, account, snapshot, root, lock_path, {source},
                   lambda *_: pytest.fail("completed receipt must avoid GET"))[0].status == "recovered"
        assert db.execute("PRAGMA synchronous").fetchone() == (1,)
    finally:
        db.set_trace_callback(None)
    # Explicit FULL barriers wrap the receipt, canonical B and terminal
    # journal transition, with COMMIT before restoring ordinary WAL mode.
    for action in ("INSERT INTO acquisition_ledger(", "INSERT INTO artifacts(",
                   "UPDATE acquisition_ledger SET status='complete'"):
        index = next(i for i, sql in enumerate(seen) if sql.startswith(action))
        before = seen[:index]
        full = max(i for i, sql in enumerate(before) if sql == "PRAGMA synchronous=FULL")
        begin = max(i for i, sql in enumerate(before) if sql == "BEGIN IMMEDIATE")
        assert full < begin
        assert "PRAGMA synchronous=1" not in seen[full:index]
        commit = next(i for i in range(index, len(seen)) if seen[i] == "COMMIT")
        restore = next(i for i in range(index, len(seen)) if seen[i] == "PRAGMA synchronous=1")
        assert commit < restore
    assert service.calls == 1 and rows(storage) == (1, 1, 1, 1)
    assert journal(storage)[0][:2] == ("complete", "sha256:" + SHA)


@pytest.mark.parametrize("case", ["no_publication", "wrong_provider_receipt", "missing_adept_proof"])
def test_journal_cannot_claim_complete_without_exact_committed_B(rig, monkeypatch, case):
    from cifereca.core.canonical_publication import publish_completed_acquisition

    service, account, source, snapshot, root, lock_path = rig
    offer = _staged(rig, monkeypatch)
    storage = service.storage
    before = journal(storage)
    assert rows(storage) == (0, 0, 0, 0)
    if case != "no_publication":
        path = root / "A" / "book.pdf"
        publish_completed_acquisition(
            storage, library_root=root, path=path, source=source,
            snapshot=snapshot, final_sha256=SHA,
            delivery_receipt_ref=(
                "wrong-proof" if case == "wrong_provider_receipt"
                else "synthetic-completed-delivery-" + source.right_id
            ),
        )
        if case == "missing_adept_proof":
            record = json.loads(before[0][2])
            record["adept_receipt"] = "ADEPT-proof-not-published"
            with storage._lock, storage._connect() as sql:
                sql.execute(
                    "UPDATE acquisition_ledger SET metadata_json=? "
                    "WHERE acquisition_id LIKE 'canonical-intake:%'",
                    (json.dumps(record),),
                )
            before = journal(storage)
    with InterProcessLock(lock_path) as lock:
        with pytest.raises(ValueError, match="canonical_intake_published_origin_unverified"):
            recovery.mark_completed_delivery_published(
                storage, source=source, offer_id=offer, artifact_id="sha256:" + SHA,
                shared_lock=lock,
            )
    assert journal(storage) == before
    assert (root / "A" / "book.pdf").read_bytes() == BYTES
    if case == "no_publication":
        assert rows(storage) == (0, 0, 0, 0)
        assert run(service, account, snapshot, root, lock_path, {source},
                   lambda *_: pytest.fail("persisted final receipt must avoid GET")
                   )[0].status == "recovered"
        assert journal(storage)[0][:2] == ("complete", "sha256:" + SHA)
    else:
        assert rows(storage)[-1] == 1


def test_journal_terminal_requires_exact_provider_and_adept_origins(rig, monkeypatch):
    from cifereca.core.canonical_publication import publish_completed_acquisition

    service, account, source, snapshot, root, lock_path = rig
    offer = _staged(rig, monkeypatch)
    db = service.storage
    # The independent publisher consumes only a verified staged file and
    # never invents the proof carried in the recovery journal.
    document = json.loads(journal(db)[0][2])
    document["adept_receipt"] = "synthetic-adept-final"
    with db._lock, db._connect() as sql:
        sql.execute("UPDATE acquisition_ledger SET metadata_json=? "
                    "WHERE acquisition_id LIKE 'canonical-intake:%'",
                    (json.dumps(document),))
    publish_completed_acquisition(
        db, library_root=root, path=root / "A" / "book.pdf",
        source=source, snapshot=snapshot, final_sha256=SHA,
        delivery_receipt_ref=document["receipt"],
        adept_receipt_ref=document["adept_receipt"],
    )
    with InterProcessLock(lock_path) as lock:
        recovery.mark_completed_delivery_published(
            db, source=source, offer_id=offer,
            artifact_id="sha256:" + SHA, shared_lock=lock,
        )
        # Status transition is idempotent; never duplicates B.
        recovery.mark_completed_delivery_published(
            db, source=source, offer_id=offer,
            artifact_id="sha256:" + SHA, shared_lock=lock,
        )
    assert journal(db)[0][:2] == ("complete", "sha256:" + SHA)
    assert rows(db) == (1, 1, 1, 2)
