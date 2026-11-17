"""Targeted admitted source writer -> journal, without auto-executing work."""
import pytest

from cifereca.core import notice_c_job_journal as journal
from cifereca.core.notice_c_cover_index import (
    persist_exact_public_search_cover, read_consolidated_notice_cover,
)
from cifereca.core.notice_c_work_requests import (
    persist_public_source_and_request, request_affected_public_recompute,
)
from cifereca.core.notice_search_cover_producer import admit_public_search_catalogue
from cifereca.core.notice_cover_repository import read_notice_cover
from cifereca.storage import Storage
from tests.test_core_p0_6366_public_c_sqlite_source_search import persist, verified_public
from tests.test_core_p0_4982_origin_public_recompute import setup_group, NEW_URL


def write(db, row, at):
    observation = admit_public_search_catalogue(
        row, identity_id="alice", observed_at=at,
        public_cover_url=row.thumbnail_url,
    )
    assert observation is not None
    return persist_public_source_and_request(
        db, observation, field_path=("search_result", "thumbnail_url"),
        cover_inventory_complete=True, now=at,
    )


def test_targeted_source_change_fences_old_job_and_preserves_other_c(tmp_path):
    db = Storage(tmp_path / "targeted.sqlite")
    try:
        def row(title, record="same"):
            return verified_public(record_id=record, title=title)
        initial = persist(db, row("Ancien"), at=10)
        own = persist_exact_public_search_cover(db, initial)
        other = persist_exact_public_search_cover(db, persist(db, row("Témoin", "other"), at=10))
        unchanged, empty = write(db, row("Ancien"), 11)
        assert unchanged == initial and not empty.refs
        changed, first = write(db, row("Nouveau"), 12)
        assert len(first.refs) == 1 and first.refused == 0
        ref, = first.refs
        lease = journal.claim_job(db, ref, now=12, lease_seconds=20)
        assert lease is not None
        assert request_affected_public_recompute(db, changed, now=12).refs == (ref,)
        latest, second = write(db, row("Encore nouveau"), 13)
        successor, = second.refs
        assert successor.key == ref.key and successor.generation == ref.generation + 1
        with db._lock, db._connect() as sql:
            sql.execute("BEGIN IMMEDIATE")
            with pytest.raises(ValueError, match="generation_changed"):
                journal._complete_job_txn(sql, lease, now=14)
        with pytest.raises(ValueError, match="source_changed"):
            request_affected_public_recompute(db, changed, now=14)
        assert read_notice_cover(db, latest.document) == latest
        assert read_consolidated_notice_cover(db, own.document) == own
        assert read_consolidated_notice_cover(db, other.document) == other
        assert journal.read_job(db, successor).attempts == 0
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        db.close()


def test_source_and_request_rollback_together(tmp_path, monkeypatch):
    from cifereca.core import notice_c_work_requests as requests
    db = Storage(tmp_path / "rollback.sqlite")
    try:
        old = persist(db, verified_public(record_id="rollback", title="Ancien"), at=10)
        own = persist_exact_public_search_cover(db, old)
        original = requests.request_affected_public_recompute_txn
        captured = []
        def crash(sql, source, *, now):
            result = original(sql, source, now=now)
            captured.extend(result.refs)
            raise RuntimeError("crash")
        monkeypatch.setattr(requests, "request_affected_public_recompute_txn", crash)
        with pytest.raises(RuntimeError, match="crash"):
            write(db, verified_public(record_id="rollback", title="Nouveau"), 11)
        assert len(captured) == 1
        assert read_notice_cover(db, old.document) == old
        assert read_consolidated_notice_cover(db, own.document) == own
        with pytest.raises(ValueError, match="generation_changed"):
            journal.read_job(db, captured[0])
        with db._lock, db._connect() as sql:
            with pytest.raises(ValueError, match="transaction_required"):
                original(sql, old, now=12)
    finally:
        db.close()


def test_source_change_targets_self_and_origin_group_only(tmp_path, monkeypatch):
    db = Storage(tmp_path / "multi.sqlite")
    try:
        group = setup_group(db, tmp_path, monkeypatch)
        row = verified_public(
            source_provider="seller-b", record_id="edition-b",
            title="L'ouvrage commun", thumbnail_url=NEW_URL,
        )
        source, result = write(db, row, 201)
        assert result.examined == 2 and result.refused == 0
        assert len(result.refs) == 2
        from cifereca.core import notice_c_exact_recompute as exact
        from cifereca.core import notice_c_origin_recompute as origin
        jobs = [journal.read_job(db, ref) for ref in result.refs]
        assert {job.spec.implementation_digest for job in jobs} == {
            exact.IMPLEMENTATION_DIGEST, origin.IMPLEMENTATION_DIGEST,
        }
        assert group.document.source_key in {job.spec.target for job in jobs}
        assert {c.url for c in read_consolidated_notice_cover(db, group.document).candidates} != {NEW_URL}
        assert all(job.attempts == 0 for job in jobs)
        assert request_affected_public_recompute(db, source, now=201).refs == result.refs
    finally:
        db.close()
