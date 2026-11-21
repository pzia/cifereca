"""#6937: real SQLite atomic compaction without history loss or quota refund."""
from dataclasses import replace
from pathlib import Path

import pytest

from cifereca.core import derived_variant_compact_receipt as compact
from cifereca.core import derived_variant_compact_shadow as shadow
from cifereca.core import derived_variant_journal as journal
from cifereca.core import derived_variant_retention_convergence as convergence
from cifereca.core import derived_variant_capacity_status as capacity
from cifereca.core import derived_variant_private_retirement as physical
from cifereca.storage import Storage
from tests.test_core_p0_6937_compact_shadow import committed
from tests.test_core_p0_6937_private_retirement import rig, isolated_fence, _published, _prepare


def _compact_args(args, proof):
    return dict(args, approved_compact_sha256=journal._receipt_digest(proof))


def test_authoritative_compaction_preserves_all_logical_readers_and_budget(committed, monkeypatch):
    db, intent, placement, key, raw, pk, proof, args = committed
    marker = compact._RETIRED_PREFIX + args["receipt_sha256"]
    before = journal.summarize_derived_journal(db)
    assert shadow.write_compact_shadow(db, **args)["status"] == "shadow_written"
    old_capacity = capacity.review_derived_capacity_status(db)
    assert old_capacity["compact_only_receipts"] == 0
    assert shadow.retire_compact_original(db, **_compact_args(args, proof)) == {
        "status": "logical_original_retired", "original_retained": False,
        "receipt_reconstructible": True, "retention_authorized": False,
        "quota_refund_authorized": False, "destructive_cleanup_allowed": False}
    reopened = Storage(db.path)
    try:
        assert reopened.metadata_get(key) is None
        assert reopened.metadata_get(pk) == proof
        assert reopened.metadata_get(marker) == journal._receipt_digest(proof)
        with reopened._lock, reopened._connect() as sql:
            sql.execute("BEGIN")
            assert journal._read_logical_receipt(sql, key) == raw
            assert compact._retired_receipts(sql) == {key: raw}
            assert compact._measure_compact_in_transaction(sql, require_paired=True)[0] == 1
        assert journal.summarize_derived_journal(reopened) == before
        after = capacity.review_derived_capacity_status(reopened)
        assert after["paired_compact_shadows"] == 0 and after["compact_only_receipts"] == 1
        assert after["compact_retirement_payload_bytes"] == 64
        assert after["physical_original_payload_bytes"] == 0
        assert after["receipt_payload_bytes"] == old_capacity["receipt_payload_bytes"]
        assert after["declared_output_bytes"] == old_capacity["declared_output_bytes"]
        observed = convergence.review_converged_derived_retention(
            reopened, library_root=args["library_root"], identity_id=args["identity_id"],
            explicitly_requested=True)
        assert observed["counts"]["linked_pair_observed"] == 1
        assert reopened.placement_get(placement)["path"] == intent.candidate_path
        assert Path(intent.staged_path).samefile(intent.candidate_path)
        # The original's canonical target remains reserved, even though
        # its physical metadata row no longer exists.
        new = replace(intent, options_fingerprint="compacted-target")
        with pytest.raises(journal.DerivedJournalConflict, match="slot_occupied"):
            journal.reserve_derived_publication(reopened, new)
        monkeypatch.setattr(journal, "_MAX_RECORDS", 1)
        new = replace(new, candidate_path=str(Path(intent.candidate_path).with_name("new.epub")))
        with pytest.raises(journal.DerivedJournalConflict, match="capacity_exceeded"):
            journal.reserve_derived_publication(reopened, new)
        with pytest.raises(journal.DerivedJournalConflict):
            shadow.retire_compact_original(reopened, **_compact_args(args, proof))
    finally:
        reopened.close()


@pytest.mark.parametrize("case", ["missing_shadow", "wrong_proof", "scope", "sql_fault", "nested"])
def test_compaction_refusal_preserves_authoritative_original(committed, case):
    db, intent, placement, key, raw, pk, proof, args = committed
    if case != "missing_shadow":
        shadow.write_compact_shadow(db, **args)
    options = _compact_args(args, proof)
    marker = compact._RETIRED_PREFIX + args["receipt_sha256"]
    sql = db._connect()
    if case == "wrong_proof":
        options["approved_compact_sha256"] = "0" * 64
    elif case == "scope":
        options["scope_current"] = lambda conn: conn.execute(
            "SELECT 1 FROM metadata WHERE key=?", (marker,)).fetchone() is None
    elif case == "sql_fault":
        with db._lock, db._connect() as conn:
            conn.execute("CREATE TRIGGER synthetic_compact_abort AFTER DELETE ON metadata "
                         "WHEN OLD.key LIKE 'derived.publication.committed.v2:%' "
                         "BEGIN SELECT RAISE(ABORT,'synthetic'); END")
    elif case == "nested":
        sql.execute("BEGIN IMMEDIATE")
        sql.execute("INSERT INTO metadata(key,value) VALUES('test.compact.caller','pending')")
    try:
        with pytest.raises(journal.DerivedJournalConflict):
            shadow.retire_compact_original(db, **options)
        if case == "nested":
            assert sql.in_transaction
            assert sql.execute("SELECT value FROM metadata WHERE key='test.compact.caller'").fetchone() == ("pending",)
            sql.rollback()
        assert db.metadata_get(key) == raw and db.metadata_get(marker) is None
        assert db.metadata_get(pk) == (None if case == "missing_shadow" else proof)
        assert Path(intent.staged_path).samefile(intent.candidate_path)
    finally:
        if sql.in_transaction:
            sql.rollback()


@pytest.mark.parametrize("case", ["marker_changed", "proof_absent", "original_reappears", "marker_absent"])
def test_corrupt_compact_only_never_grants_free_admission(committed, case):
    db, intent, placement, key, raw, pk, proof, args = committed
    shadow.write_compact_shadow(db, **args)
    shadow.retire_compact_original(db, **_compact_args(args, proof))
    marker = compact._RETIRED_PREFIX + args["receipt_sha256"]
    if case == "marker_changed":
        db.metadata_set(marker, "0" * 64)
    elif case == "original_reappears":
        db.metadata_set(key, raw)
    else:
        with db._lock, db._connect() as sql:
            sql.execute("DELETE FROM metadata WHERE key=?", (pk if case == "proof_absent" else marker,))
    with pytest.raises(journal.DerivedJournalConflict):
        capacity.review_derived_capacity_status(db)
    with pytest.raises(journal.DerivedJournalConflict):
        journal.reserve_derived_publication(db, replace(
            intent, options_fingerprint="new", candidate_path=str(Path(intent.candidate_path).with_name("new.epub"))))
    assert Path(intent.staged_path).samefile(intent.candidate_path)


def test_v3_logical_compaction_then_supervised_physical_retirement(rig, isolated_fence):
    db, intent, stage, options = _published(rig)
    digest = options["receipt_sha256"]
    key = journal._COMMITTED + digest
    raw = db.metadata_get(key)
    proof_key, proof = compact.encode_committed_witness(key, raw)
    args = {k: v for k, v in options.items() if k != "approved_binding_sha256"}
    args["approved_receipt_sha256"] = digest
    assert shadow.write_compact_shadow(db, **args)["status"] == "shadow_written"
    shadow.retire_compact_original(db, **_compact_args(args, proof))
    assert db.metadata_get(key) is None
    before = journal.summarize_derived_journal(db)
    approved = _prepare(db, options)
    assert physical.retire_private_stage(db, **approved)["outcome"] == "unlink_confirmed"
    assert not stage.exists() and Path(intent.candidate_path).is_file()
    assert db.metadata_get(proof_key) == proof and db.metadata_get(key) is None
    assert journal.summarize_derived_journal(db) == before
    with db._lock, db._connect() as sql:
        sql.execute("BEGIN")
        assert journal._read_logical_receipt(sql, key) == raw
    assert len(db.artifact_variants()) == 1 and len(db.placements()) == 2
