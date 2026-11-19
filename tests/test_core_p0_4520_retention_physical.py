"""#4520: committed physical witness never authorizes reclamation."""
from pathlib import Path
import os

import pytest

from cifereca.core import derived_variant_journal as journal
from cifereca.core import derived_variant_retention_physical as physical
from tests.test_core_p0_4520_derived_journal import _prepared_publication, _publish_reserved


def _review(service, root):
    return physical.review_committed_derived_physical(
        service.storage, library_root=root, identity_id="id1", explicitly_requested=True,
    )


@pytest.mark.parametrize("change, category", [
    ("none", "linked_pair_observed"),
    ("stage_absent", "stage_absent_target_verified"),
    ("extra_link", "additional_links_observed"),
    ("extra_link_stage_absent", "additional_links_observed"),
    ("stage_symlink", "unverified"),
    ("stage_copy", "unverified"),
    ("stage_directory_absent", "unverified"),
    ("target_copy", "unverified"),
    ("parent_changed", "unverified"),
])
def test_exact_committed_physical_observation(tmp_path, change, category):
    service, intent, reservation = _prepared_publication(tmp_path)
    db = service.storage
    stage, target, parent = map(Path, (
        intent.staged_path, intent.candidate_path, intent.parent_path,
    ))
    try:
        assert _review(service, tmp_path)["committed_receipts"] == 0
        _publish_reserved(db, intent, reservation)
        before = journal.summarize_derived_journal(db)
        if change in ("stage_absent", "stage_symlink", "stage_copy", "extra_link_stage_absent"):
            stage.unlink()
        if change == "stage_symlink":
            stage.symlink_to(target)
        if change == "stage_copy":
            stage.write_bytes(target.read_bytes())
        if change in ("extra_link", "extra_link_stage_absent"):
            os.link(target, target.with_name("another.epub"))
        if change == "stage_directory_absent":
            stage.parent.rename(stage.parent.with_name("offline"))
        if change == "target_copy":
            target.unlink()
            target.write_bytes(b"new bytes")
        if change == "parent_changed":
            parent.write_bytes(b"changed")
        report = _review(service, tmp_path)
        assert report["status"] == "observed"
        assert report["committed_receipts"] == report["checked"] == 1
        assert report["counts"][category] == 1
        assert sum(report["counts"].values()) == 1
        assert report["complete_committed_page"]
        assert not any(report[k] for k in (
            "complete_physical_inventory", "canonical_dependencies_verified",
            "reclaimable_bytes_known", "retention_authorized",
            "quota_refund_authorized", "destructive_cleanup_allowed",
        ))
        assert str(tmp_path) not in repr(report)
        assert reservation.receipt_sha256 not in repr(report)
        assert journal.summarize_derived_journal(db) == before
        assert len(db.placements()) == 2
        assert target.read_bytes() == b"new bytes"
    finally:
        db.close()


def test_budget_exhaustion_is_incomplete_not_absence(tmp_path, monkeypatch):
    service, intent, reservation = _prepared_publication(tmp_path)
    db = service.storage
    try:
        _publish_reserved(db, intent, reservation)
        monkeypatch.setattr(physical, "_MAX_BYTES", 1)
        report = _review(service, tmp_path)
        assert report["status"] == "observed"
        assert report["committed_receipts"] == 1 and report["checked"] == 0
        assert not report["complete_committed_page"]
        assert not report["retention_authorized"]
    finally:
        db.close()


def test_changed_journal_discards_observations(tmp_path, monkeypatch):
    service, intent, reservation = _prepared_publication(tmp_path)
    db = service.storage
    try:
        _publish_reserved(db, intent, reservation)
        original = physical._snapshot
        calls = 0

        def changing(storage, identity):
            nonlocal calls
            calls += 1
            directory, rows = original(storage, identity)
            return directory, rows if calls == 1 else ()

        monkeypatch.setattr(physical, "_snapshot", changing)
        report = _review(service, tmp_path)
        assert report["status"] == "evidence_changed"
        assert report["checked"] == 0
        assert not report["retention_authorized"]
    finally:
        db.close()


def test_transaction_scope_and_explicit_request_are_required(tmp_path):
    service, intent, reservation = _prepared_publication(tmp_path)
    db = service.storage
    sql = db._connect()
    try:
        assert physical.review_committed_derived_physical(
            db, library_root=tmp_path, identity_id="id1",
        )["status"] == "unavailable"
        sql.execute("BEGIN IMMEDIATE")
        sql.execute("INSERT INTO metadata(key,value) VALUES(?,?)",
                    ("test.retention.physical.uncommitted", "private"))
        assert _review(service, tmp_path)["status"] == "unavailable"
        assert sql.in_transaction
        sql.rollback()
        assert db.metadata_get("test.retention.physical.uncommitted") is None
        assert physical.review_committed_derived_physical(
            db, library_root=tmp_path, identity_id="other", explicitly_requested=True,
        )["status"] == "unavailable"
    finally:
        if sql.in_transaction:
            sql.rollback()
        db.close()
