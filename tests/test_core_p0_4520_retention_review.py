"""#4520: read-only exact canonical retention witness, not authorization."""
from dataclasses import replace
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
