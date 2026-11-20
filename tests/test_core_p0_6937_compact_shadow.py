"""#6937: durable exact shadow preparation; no physical or logical retirement."""
from dataclasses import asdict, replace
from pathlib import Path
import os

import pytest

from cifereca.core import derived_variant_compact_receipt as compact
from cifereca.core import derived_variant_compact_shadow as shadow
from cifereca.core import derived_variant_journal as journal
from cifereca.core import derived_variant_supervised_recovery as recovery
from cifereca.locking import InterProcessLock
from cifereca.storage import Storage
from tests.test_core_p0_4520_recovery_operator import _setup
from tests.test_core_p0_4520_derived_journal import _publish_reserved


@pytest.fixture
def committed(tmp_path, monkeypatch):
    service, intent, reservation = _setup(tmp_path)
    db = service.storage
    lock_path = tmp_path / "acquisition.lock"
    monkeypatch.setattr(recovery, "acquisition_lock_file", lambda: lock_path)
    try:
        placement = _publish_reserved(db, intent, reservation)
        key = journal._COMMITTED + reservation.receipt_sha256
        raw = db.metadata_get(key)
        proof_key, proof = compact.encode_committed_witness(key, raw)
        with InterProcessLock(lock_path) as lock:
            args = dict(library_root=tmp_path, identity_id="id1",
                        receipt_sha256=reservation.receipt_sha256,
                        approved_receipt_sha256=reservation.receipt_sha256,
                        shared_lock=lock, scope_current=lambda sql: sql.in_transaction,
                        explicitly_requested=True)
            yield db, intent, placement, key, raw, proof_key, proof, args
    finally:
        db.close()


def test_durable_shadow_and_idempotent_replay(committed):
    db, intent, placement, key, raw, pk, proof, args = committed
    before = journal.summarize_derived_journal(db)
    sync = db._connect().execute("PRAGMA synchronous").fetchone()
    result = shadow.write_compact_shadow(db, **args)
    assert result["status"] == "shadow_written" and result["original_retained"]
    assert not any(result[k] for k in ("retention_authorized", "quota_refund_authorized",
                                       "destructive_cleanup_allowed"))
    assert db._connect().execute("PRAGMA synchronous").fetchone() == sync
    other = Storage(db.path)
    try:
        assert other.metadata_get(key) == raw and other.metadata_get(pk) == proof
        assert compact.decode_committed_witness(pk, proof) == (key, raw)
        assert journal.summarize_derived_journal(other) == before
        assert compact.review_compact_capacity(other)["count"] == 1
        assert shadow.write_compact_shadow(other, **args)["status"] == "shadow_already_present"
        assert other.placement_get(placement)["path"] == intent.candidate_path
        assert Path(intent.staged_path).samefile(intent.candidate_path)
    finally:
        other.close()


@pytest.mark.parametrize("case", ["approval", "wrong_owner", "stale_scope", "capacity",
                                  "conflicting_shadow", "stage_absent", "extra_link",
                                  "changed_target", "canonical_missing", "ambiguous_receipt"])
def test_refused_shadow_preserves_authoritative_receipt(committed, monkeypatch, case):
    db, intent, placement, key, raw, pk, proof, args = committed
    stage, target = map(Path, (intent.staged_path, intent.candidate_path))
    if case == "approval":
        args["explicitly_requested"] = False
    elif case == "wrong_owner":
        args["identity_id"] = "another"
    elif case == "stale_scope":
        args["scope_current"] = lambda sql: False
    elif case == "capacity":
        monkeypatch.setattr(compact, "_MAX_PROOFS", 0)
    elif case == "conflicting_shadow":
        db.metadata_set(pk, "unreadable")
    elif case == "stage_absent":
        stage.unlink()
    elif case == "extra_link":
        os.link(stage, stage.with_name("alias.epub"))
    elif case == "changed_target":
        target.unlink()
        target.write_bytes(b"new bytes")
    elif case == "canonical_missing":
        with db._lock, db._connect() as sql:
            sql.execute("UPDATE placements SET state='missing' WHERE placement_id=?", (placement,))
    elif case == "ambiguous_receipt":
        prior = replace(intent, parent_placement_id="other-placement")
        db.metadata_set(journal._slot(prior), journal._encode({
            "schema": "derived.intent.v2", "nonce": "f" * 32, "intent": asdict(prior),
        }))
    before = db.metadata_get(pk)
    target_bytes = target.read_bytes()
    with pytest.raises(journal.DerivedJournalConflict):
        shadow.write_compact_shadow(db, **args)
    assert db.metadata_get(key) == raw and db.metadata_get(pk) == before
    assert target.read_bytes() == target_bytes
    if case != "stage_absent":
        assert stage.read_bytes() == b"new bytes"


def test_fault_after_insert_rolls_back_then_exact_retry(committed, monkeypatch):
    db, intent, placement, key, raw, pk, proof, args = committed
    check = args["scope_current"]

    def crash(sql):
        if sql.execute("SELECT 1 FROM metadata WHERE key=?", (pk,)).fetchone():
            raise RuntimeError("synthetic crash after INSERT")
        return check(sql)

    args["scope_current"] = crash
    before = journal.summarize_derived_journal(db)
    with pytest.raises(journal.DerivedJournalConflict):
        shadow.write_compact_shadow(db, **args)
    other = Storage(db.path)
    try:
        assert other.metadata_get(pk) is None and other.metadata_get(key) == raw
        assert journal.summarize_derived_journal(other) == before
    finally:
        other.close()
    args["scope_current"] = check
    assert shadow.write_compact_shadow(db, **args)["status"] == "shadow_written"
    assert Path(intent.staged_path).samefile(intent.candidate_path)
