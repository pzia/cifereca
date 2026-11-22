"""#4520: read-only exact canonical retention witness, not authorization."""
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from cifereca.core import derived_variant_journal as journal
from cifereca.core import derived_variant_retention_review as review
from tests.test_core_p0_4520_derived_journal import _prepared_publication, _publish_reserved


def _review(db, **kw):
    return review.review_derived_receipt_retention(
        db, identity_id="id1", explicitly_requested=True, **kw,
    )


def test_committed_witness_never_refunds_or_exposes_coordinates(tmp_path):
    service, intent, reservation = _prepared_publication(tmp_path)
    db = service.storage
    try:
        assert _review(db)["counts"]["canonical_sql_witness"] == 0
        _publish_reserved(db, intent, reservation)
        before = journal.summarize_derived_journal(db)
        raw = db.metadata_get(journal._COMMITTED + reservation.receipt_sha256)
        report = _review(db)
        assert report["status"] == "observed_sql_only"
        assert report["counts"]["canonical_sql_witness"] == 1
        assert not any(report[k] for k in (
            "physical_proof_verified", "all_dependencies_verified", "retention_authorized",
            "quota_refund_authorized", "destructive_cleanup_allowed",
        ))
        assert str(tmp_path) not in repr(report)
        assert reservation.receipt_sha256 not in repr(report)
        assert journal.summarize_derived_journal(db) == before
        assert db.metadata_get(journal._COMMITTED + reservation.receipt_sha256) == raw
        assert Path(intent.staged_path).samefile(intent.candidate_path)
    finally:
        db.close()


@pytest.mark.parametrize("damage", ["origin", "parent", "child"])
def test_incomplete_sql_proof_is_not_a_retention_witness(tmp_path, damage):
    service, intent, reservation = _prepared_publication(tmp_path)
    db = service.storage
    try:
        placement = _publish_reserved(db, intent, reservation)
        with db._lock, db._connect() as sql:
            if damage == "origin":
                sql.execute("UPDATE origin_links SET metadata_json=? WHERE origin_kind='derived'", ("{}",))
            else:
                sql.execute("UPDATE placements SET state='missing' WHERE placement_id=?",
                            (intent.parent_placement_id if damage == "parent" else placement,))
        before = journal.summarize_derived_journal(db)
        result = _review(db)
        assert result["counts"]["canonical_unverified"] == 1
        assert not result["retention_authorized"]
        assert journal.summarize_derived_journal(db) == before
    finally:
        db.close()


def test_shared_stage_with_pending_receipt_is_blocked(tmp_path):
    service, intent, reservation = _prepared_publication(tmp_path)
    db = service.storage
    try:
        _publish_reserved(db, intent, reservation)
        pending = journal.reserve_derived_publication(db, replace(
            intent, options_fingerprint="new",
            candidate_path=str(Path(intent.candidate_path).with_name("next.epub")),
        ))
        before = journal.summarize_derived_journal(db)
        result = _review(db)
        assert result["counts"]["dependent_receipt_or_staging_placement"] == 1
        assert result["counts"]["canonical_sql_witness"] == 0
        assert db.metadata_get(pending.key) is not None
        assert journal.summarize_derived_journal(db) == before
    finally:
        db.close()


def test_corrupt_or_foreign_scope_does_not_claim_an_empty_inventory(tmp_path):
    service, intent, reservation = _prepared_publication(tmp_path)
    db = service.storage
    try:
        assert review.review_derived_receipt_retention(db, identity_id="id1")["status"] == "unavailable"
        assert review.review_derived_receipt_retention(
            db, identity_id="missing", explicitly_requested=True,
        )["status"] == "unavailable"
        db.metadata_set(reservation.key, "invalid")
        assert _review(db)["status"] == "unavailable"
        assert db.metadata_get(reservation.key) == "invalid"
    finally:
        db.close()


def test_nested_snapshot_never_commits_caller_transaction(tmp_path):
    service, intent, reservation = _prepared_publication(tmp_path)
    db = service.storage
    sql = db._connect()
    try:
        sql.execute("BEGIN IMMEDIATE")
        sql.execute("INSERT INTO metadata(key,value) VALUES(?,?)", ("test.retention.uncommitted", "private"))
        assert _review(db)["status"] == "unavailable"
        assert sql.in_transaction
        sql.rollback()
        assert db.metadata_get("test.retention.uncommitted") is None
    finally:
        if sql.in_transaction:
            sql.rollback()
        db.close()


@pytest.mark.parametrize("relation", ["output_alias", "input_alias", "different_inode"])
def test_recorded_inode_dependency_agrees_in_both_retention_reviews(tmp_path, relation):
    from cifereca.core import derived_variant_retention_convergence as combined

    service, intent, reservation = _prepared_publication(tmp_path)
    db = service.storage
    try:
        _publish_reserved(db, intent, reservation)
        # Synthetic pending receipt with fully distinct paths. Recorded inode
        # identity, not equal content or a shared parent path, is the veto.
        output = intent.output_stamp
        parent = intent.parent_stamp
        distinct_inode = (output[0], output[1] + 1000000, *output[2:])
        other = replace(
            intent, options_fingerprint="inode-dependency",
            parent_path=str(Path(intent.parent_path).with_name("other-parent.epub")),
            staged_path=str(Path(intent.staged_path).with_name("other-stage.epub")),
            candidate_path=str(Path(intent.candidate_path).with_name("other-output.epub")),
            output_stamp=output if relation == "output_alias" else distinct_inode,
            parent_stamp=(
                (output[0], output[1], *parent[2:])
                if relation == "input_alias" else parent
            ),
        )
        # Model historical journal evidence containing an alias, not a new
        # valid reservation: live reservation independently checks parent
        # placement coordinates, which this synthetic witness changes.
        pending_key = journal._slot(other)
        pending_raw = journal._encode({
            "schema": "derived.intent.v2", "nonce": "f" * 32,
            "intent": asdict(other),
        })
        db.metadata_set(pending_key, pending_raw)
        before = journal.summarize_derived_journal(db)
        report = _review(db)
        _, _, owned = combined._snapshot(db, intent.identity_id)
        _, _, _, verdict = owned[0]
        dependent = relation != "different_inode"
        assert report["counts"]["dependent_receipt_or_staging_placement"] == int(dependent)
        if dependent:
            assert verdict == "known_dependency"
        else:
            assert report["counts"]["canonical_sql_witness"] == 1
            # Broader dependency scanner may still be conservative.
            assert verdict in ("canonical", "known_dependency", "external_dependency_unverified")
        assert not report["retention_authorized"]
        assert db.metadata_get(pending_key) == pending_raw
        assert journal.summarize_derived_journal(db) == before
        assert Path(intent.staged_path).samefile(intent.candidate_path)
    finally:
        db.close()


@pytest.mark.parametrize("directory", [
    "profile\nunsafe", "profile\x7funsafe", "x" * 257,
    "profile\x85unsafe", "profile\u202eunsafe",
    "profile\u2028unsafe", "profile\u200dunsafe",
    "profile\u2066unsafe", "profile\x1bunsafe",
])
def test_retention_scope_rejects_malformed_profile_directory(tmp_path, directory):
    service, intent, reservation = _prepared_publication(tmp_path)
    db = service.storage
    try:
        before = journal.summarize_derived_journal(db)
        with db._lock, db._connect() as sql:
            sql.execute(
                "UPDATE identities SET directory_name=? WHERE identity_id=?",
                (directory, "id1"),
            )
        result = _review(db)
        assert result["status"] == "unavailable"
        assert not result["retention_authorized"]
        assert not result["quota_refund_authorized"]
        assert not result["destructive_cleanup_allowed"]
        assert journal.summarize_derived_journal(db) == before
    finally:
        db.close()
