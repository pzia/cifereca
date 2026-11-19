"""#4520: exact correlated retention evidence; synthetic only."""
from dataclasses import replace
import hashlib
import os
from pathlib import Path

from cifereca.core import derived_variant_journal as journal
from cifereca.core import derived_variant_retention_convergence as review
from tests.test_core_p0_4520_derived_journal import _prepared_publication, _publish_reserved


def _review(db, root, **kwargs):
    return review.review_converged_derived_retention(
        db, library_root=root, identity_id="id1", explicitly_requested=True, **kwargs,
    )


def test_sql_and_physical_witness_cannot_be_borrowed_across_receipts(tmp_path):
    service, intent, first = _prepared_publication(tmp_path)
    db = service.storage
    try:
        placement = _publish_reserved(db, intent, first)
        stage = Path(intent.staged_path).with_name("second-stage.epub")
        target = Path(intent.candidate_path).with_name("second.epub")
        stage.write_bytes(b"second derivative")
        info = stage.stat()
        other = replace(intent, staged_path=str(stage), candidate_path=str(target),
                        output_sha256=hashlib.sha256(stage.read_bytes()).hexdigest(),
                        output_size=info.st_size,
                        output_stamp=(info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns),
                        options_fingerprint="second")
        second = journal.reserve_derived_publication(db, other)
        os.link(stage, target)
        _publish_reserved(db, other, second)
        with db._lock, db._connect() as sql:
            sql.execute("UPDATE placements SET state='missing' WHERE placement_id=?", (placement,))
        stage.unlink()
        stage.write_bytes(target.read_bytes())
        before = journal.summarize_derived_journal(db)
        result = _review(db, tmp_path)
        assert result["status"] == "observed" and result["checked"] == 2
        assert result["counts"]["canonical_sql_unverified"] == 1
        assert result["counts"]["physical_unverified"] == 1
        assert result["counts"]["linked_pair_observed"] == 0
        assert not any(result[k] for k in (
            "retention_authorized", "quota_refund_authorized", "destructive_cleanup_allowed",
            "all_dependencies_verified", "reclaimable_bytes_known",
        ))
        assert first.receipt_sha256 not in repr(result)
        assert str(tmp_path) not in repr(result)
        assert journal.summarize_derived_journal(db) == before
    finally:
        db.close()


def test_positive_witness_is_observation_only_and_shared_stage_blocks(tmp_path):
    service, intent, reservation = _prepared_publication(tmp_path)
    db = service.storage
    try:
        _publish_reserved(db, intent, reservation)
        before = journal.summarize_derived_journal(db)
        result = _review(db, tmp_path)
        assert result["counts"]["linked_pair_observed"] == 1
        assert not result["retention_authorized"]
        assert journal.summarize_derived_journal(db) == before
        journal.reserve_derived_publication(db, replace(
            intent, options_fingerprint="new",
            candidate_path=str(Path(intent.candidate_path).with_name("next.epub")),
        ))
        result = _review(db, tmp_path)
        assert result["counts"]["known_dependency"] == 1
        assert result["counts"]["linked_pair_observed"] == 0
    finally:
        db.close()


def test_change_during_physical_observation_discards_page(tmp_path, monkeypatch):
    service, intent, reservation = _prepared_publication(tmp_path)
    db = service.storage
    try:
        placement = _publish_reserved(db, intent, reservation)
        original = review._observe

        def damaged(intent, profile, current):
            with db._lock, db._connect() as sql:
                sql.execute("UPDATE placements SET state='missing' WHERE placement_id=?", (placement,))
            return original(intent, profile, current)

        monkeypatch.setattr(review, "_observe", damaged)
        result = _review(db, tmp_path)
        assert result["status"] == "evidence_changed"
        assert result["checked"] == 0 and not any(result["counts"].values())
    finally:
        db.close()


def test_budget_and_existing_transaction_do_not_claim_completion(tmp_path, monkeypatch):
    service, intent, reservation = _prepared_publication(tmp_path)
    db = service.storage
    sql = db._connect()
    try:
        _publish_reserved(db, intent, reservation)
        monkeypatch.setattr(review, "_MAX_BYTES", 1)
        result = _review(db, tmp_path)
        assert result["committed_receipts"] == 1 and result["checked"] == 0
        assert not result["complete_committed_page"]
        sql.execute("BEGIN IMMEDIATE")
        sql.execute("INSERT INTO metadata(key,value) VALUES(?,?)", ("test.retention.convergence", "private"))
        assert _review(db, tmp_path)["status"] == "unavailable"
        assert sql.in_transaction
        sql.rollback()
        assert db.metadata_get("test.retention.convergence") is None
    finally:
        if sql.in_transaction:
            sql.rollback()
        db.close()


def test_local_symlink_observation_is_correlated_before_hash(tmp_path, monkeypatch):
    service, intent, first = _prepared_publication(tmp_path)
    db = service.storage
    try:
        _publish_reserved(db, intent, first)
        stage = Path(intent.staged_path).with_name("second-stage.epub")
        target = Path(intent.candidate_path).with_name("second.epub")
        stage.write_bytes(b"second derivative")
        info = stage.stat()
        other = replace(intent, staged_path=str(stage), candidate_path=str(target),
                        output_sha256=hashlib.sha256(stage.read_bytes()).hexdigest(),
                        output_size=info.st_size,
                        output_stamp=(info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns),
                        options_fingerprint="second")
        second = journal.reserve_derived_publication(db, other)
        os.link(stage, target)
        _publish_reserved(db, other, second)
        alias = Path(intent.parent_path).parent / "alias"
        alias.symlink_to(intent.staged_path)

        def no_hash(*args):
            raise AssertionError("an alias observation must block before hashing")

        monkeypatch.setattr(review, "_observe", no_hash)
        report = _review(db, tmp_path)
        assert report["status"] == "observed" and report["checked"] == 2
        assert report["counts"]["known_symlink_dependency"] == 1
        assert report["counts"]["symlink_dependency_unverified"] == 1
        assert report["counts"]["linked_pair_observed"] == 0
        assert not report["retention_authorized"]
    finally:
        db.close()


def test_symlink_added_during_hash_discards_whole_page(tmp_path, monkeypatch):
    service, intent, reservation = _prepared_publication(tmp_path)
    db = service.storage
    try:
        _publish_reserved(db, intent, reservation)
        original = review._observe

        def changed(intent, profile, current):
            (profile / "late-alias").symlink_to(intent.staged_path)
            return original(intent, profile, current)

        monkeypatch.setattr(review, "_observe", changed)
        report = _review(db, tmp_path)
        assert report["status"] == "evidence_changed"
        assert report["checked"] == 0 and not any(report["counts"].values())
    finally:
        db.close()


def test_incomplete_symlink_walk_never_claims_a_positive_page(tmp_path, monkeypatch):
    from cifereca.core import derived_variant_stage_symlinks as symlinks
    service, intent, reservation = _prepared_publication(tmp_path)
    db = service.storage
    try:
        _publish_reserved(db, intent, reservation)
        monkeypatch.setattr(symlinks, "_MAX_ENTRIES", 1)
        report = _review(db, tmp_path)
        assert report["status"] == "incomplete"
        assert report["checked"] == 0 and not any(report["counts"].values())
        assert not report["complete_committed_page"]
    finally:
        db.close()
