"""#4982 live graph extension through durable journal and existing pump."""
import pytest

from cifereca.core import notice_c_job_journal as journal
from cifereca.core.notice_c_cover_index import (
    persist_exact_public_search_cover, persist_consolidated_cover_source,
    read_consolidated_notice_cover,
)
from cifereca.core.notice_c_origin_relink import (
    request_origin_public_relink, prepare_origin_public_relink,
)
from cifereca.core.notice_c_work_pump import PublicNoticeWorkPump
from cifereca.storage import Storage
from cifereca.task_admission import SharedTaskAdmission
from cifereca.task_runtime import TaskBrokerImpl
from tests.test_core_p0_4982_relink_inputs import group, add_third


def request(db, ref, generation=0):
    return request_origin_public_relink(
        db, ref, now=100, expected_generation=generation,
    )


def prepare(db, job):
    return prepare_origin_public_relink(db, journal.read_job(db, job).spec)


def test_relink_dispatch_after_restart_preserves_anchor_and_self_c(tmp_path):
    path = tmp_path / "relink.sqlite"
    db = Storage(path)
    try:
        ref, pair = group(db, tmp_path)
        previous = read_consolidated_notice_cover(db, ref)
        third = add_third(db, tmp_path)
        self_c = persist_exact_public_search_cover(db, third)
        job = request(db, ref)
        assert request(db, ref) == job
        assert journal.read_job(db, job).state == "pending"
        assert read_consolidated_notice_cover(db, ref) == previous
    finally:
        db.close()

    db = Storage(path)
    tasks = TaskBrokerImpl(object(), max_workers=1, shared_admission=
                           SharedTaskAdmission(max_inflight=3, reserve_user_slots=1))
    events = []
    try:
        pump = PublicNoticeWorkPump(db, tasks, clock=lambda: 100,
                                    on_committed=lambda *event: events.append(event))
        handle, = pump.tick().handles
        assert handle.result(timeout=10) == "current"
        final = read_consolidated_notice_cover(db, ref)
        assert final.document == ref and final.epoch > previous.epoch
        assert {m.source_ref for m in final.sources} == {
            source.document.source_key for source in (*pair, third)
        }
        assert events == [(ref, final.epoch)]
        assert journal.read_job(db, job).state == "current"
        assert read_consolidated_notice_cover(db, self_c.document) == self_c
        assert db.entitlements() == [] and db.acquisitions() == []
        with pytest.raises(ValueError, match="extension_not_qualified"):
            request(db, ref, job.generation)
    finally:
        tasks.close()
        db.close()


def test_relink_rollback_and_expired_attempt_reclaim(tmp_path):
    db = Storage(tmp_path / "rollback.sqlite")
    try:
        ref, _ = group(db, tmp_path)
        previous = read_consolidated_notice_cover(db, ref)
        add_third(db, tmp_path)
        job = request(db, ref)
        lease = journal.claim_job(db, job, now=100, lease_seconds=30)
        publication = prepare(db, job)
        with pytest.raises(RuntimeError, match="crash"):
            with db._lock, db._connect() as sql:
                sql.execute("BEGIN IMMEDIATE")
                journal._held(sql, lease, 101)
                assert publication(sql)
                journal._complete_job_txn(sql, lease, now=101)
                raise RuntimeError("crash")
        assert read_consolidated_notice_cover(db, ref) == previous
        assert journal.read_job(db, job).state == "pending"
        recovered = journal.claim_job(db, job, now=130, lease_seconds=30)
        assert recovered is not None and recovered.fence == lease.fence + 1
        publication = prepare(db, job)
        with db._lock, db._connect() as sql:
            sql.execute("BEGIN IMMEDIATE")
            journal._held(sql, recovered, 131)
            assert publication(sql)
            journal._complete_job_txn(sql, recovered, now=131)
        assert len(read_consolidated_notice_cover(db, ref).sources) == 3
        assert journal.read_job(db, job).state == "current"
    finally:
        db.close()


@pytest.mark.parametrize("damage", ("origin", "competing_c"))
def test_relink_refuses_changed_relation_or_overlap_at_commit(tmp_path, damage):
    db = Storage(tmp_path / "refused.sqlite")
    try:
        ref, _ = group(db, tmp_path)
        previous = read_consolidated_notice_cover(db, ref)
        third = add_third(db, tmp_path)
        job = request(db, ref)
        publication = prepare(db, job)
        if damage == "origin":
            with db._lock, db._connect() as sql:
                sql.execute(
                    "DELETE FROM origin_links WHERE source_reference_id IN ("
                    "SELECT source_reference_id FROM source_references "
                    "WHERE provider_id='seller-c')"
                )
        else:
            persist_consolidated_cover_source(
                db, canonical_edition_id="edition:competing-relink",
                source=third, relation_proof_id="fixture:competing",
            )
        with pytest.raises(ValueError):
            with db._lock, db._connect() as sql:
                sql.execute("BEGIN IMMEDIATE")
                publication(sql)
        with pytest.raises(ValueError):
            request(db, ref, job.generation)
        assert read_consolidated_notice_cover(db, ref) == previous
        assert journal.read_job(db, job).state == "pending"
    finally:
        db.close()
