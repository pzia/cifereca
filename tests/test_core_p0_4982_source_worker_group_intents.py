"""Fetch/reparse use the same journal group ports as Search, never a new writer."""
from contextlib import nullcontext

import pytest

from cifereca.core import notice_c_cover_index as index
from cifereca.core import notice_c_initial_group_publication as initial
from cifereca.core import notice_c_job_journal as journal
from cifereca.core import notice_c_origin_link_producer as origin
from cifereca.core import notice_c_origin_recompute as recompute
from cifereca.core import notice_c_origin_relink as relink
from cifereca.core.notice_c_fetch_authority import PublicFetchAuthority
from cifereca.core.notice_c_fetch_execution import PublicFetchExecution, PublicFetchPort, ReservedPublicFetch
from cifereca.core.notice_c_work_pump import PublicNoticeWorkPump
from cifereca.core.notice_cover_repository import read_notice_cover
from cifereca.core.notice_search_cover_producer import admit_public_search_catalogue
from cifereca.storage import Storage
from tests.test_core_p0_4982_fetch_authority import Authority, TRANSPORT, IMPLEMENTATION, request as request_fetch
from tests.test_core_p0_4982_fetch_execution import broker
from tests.test_core_p0_4982_raw_input_receipt import record
from tests.test_core_p0_4982_reparse_requests import TOKEN, request as request_reparse
from tests.test_core_p0_4982_reparse_execution import FakePort
from tests.test_core_p0_4982_relink_inputs import add_third
from tests.test_core_p0_6366_origin_attested_public_c import _origins, _source_rows, witnessed, public_row
from tests.test_core_p0_6366_public_c_sqlite_source_search import persist


def jobs(db):
    with db._lock, db._connect() as sql:
        sql.execute("BEGIN")
        keys = sql.execute("SELECT key FROM metadata WHERE key>=? AND key<?",
                           (journal._PREFIX, journal._END)).fetchall()
        return tuple(journal._load(sql, key[0][len(journal._PREFIX):]) for key in keys)


@pytest.mark.parametrize("mode", ["fetch", "reparse"])
@pytest.mark.parametrize("phase", ["initial", "exact", "extend_old", "extend_new", "revoked"])
def test_authorized_source_worker_group_intents(tmp_path, mode, phase):
    path = tmp_path / "work.sqlite"
    db = Storage(path)
    tasks = broker()
    authority = None
    try:
        _origins(db, tmp_path)
        first, second = _source_rows()
        pair = tuple(persist(db, row, at=100) for row in (first, second))
        for item in pair:
            index.persist_exact_public_search_cover(db, item)
        source = pair[0]
        sources = pair
        old_job = None
        if phase == "initial":
            before = None
            ref = initial.request_initial_group_publication(db, source, now=100)
            old_job = journal.read_job(db, ref)
            digest = initial.IMPLEMENTATION_DIGEST
        else:
            before = origin.reconcile_origin_linked_public_c(db, source)
            assert before is not None and len(before.sources) == 2
            digest = recompute.IMPLEMENTATION_DIGEST
            if phase.startswith("extend"):
                third = add_third(db, tmp_path)
                index.persist_exact_public_search_cover(db, third)
                sources = (*pair, third)
                if phase == "extend_new":
                    source = third
                ref = initial.request_search_group_repair(db, third, now=100)
                old_job = journal.read_job(db, ref)
                digest = relink.IMPLEMENTATION_DIGEST
        own = index.canonical_cover_document_ref(source.observation.key.edition_id)
        old_own = index.read_consolidated_notice_cover(db, own)
        bio = source.observation.fields_copy()["search_result"]["public_bibliography"]
        changed_row = witnessed(public_row(
            source_provider=source.observation.key.provider_id, record_id=bio["record_id"],
            title=bio["title"], author=bio["author"],
            thumbnail_url=source.attribute.candidates[0].url,
            description="Résumé corrigé par le producteur",
        ))
        now = 101
        changed = admit_public_search_catalogue(
            changed_row, identity_id="alice",
            observed_at=now if mode == "fetch" else source.observation.observed_at,
            public_cover_url=changed_row.thumbnail_url,
        )
        assert changed.key == source.observation.key
        if mode == "fetch":
            authority = PublicFetchAuthority(db, clock=lambda: now)
            job_ref = request_fetch(authority, source, Authority())
            calls = []
            def reserve(permit):
                calls.append("reserve")
                def execute(control):
                    control.check_cancelled()
                    with authority.transport_start_guard(permit):
                        calls.append("execute")
                    return changed
                return ReservedPublicFetch(permit, execute, lambda: calls.append("release"), nullcontext)
            options = {"fetch_execution": PublicFetchExecution(authority, (PublicFetchPort(
                source.observation.key.provider_id, TRANSPORT, IMPLEMENTATION, reserve,
            ),))}
            expected_calls = ["reserve", "execute", "release"]
        else:
            receipt = record(db, source, producer_token=TOKEN)
            job_ref = request_reparse(db, source, receipt)
            parser = FakePort(source, receipt)
            parser.next = changed
            calls = parser.calls
            options = {"replay_registry": parser.registry()}
            expected_calls = ["read", "parse", "hold"]
        if phase == "revoked":
            with db._lock, db._connect() as sql:
                sql.execute(
                    "DELETE FROM origin_links WHERE source_reference_id IN ("
                    "SELECT source_reference_id FROM source_references WHERE provider_id='seller-b')"
                )
        pump = PublicNoticeWorkPump(db, tasks, clock=lambda: now, **options)
        outcome = pump._bridge.submit(job_ref).result(timeout=10)
        assert calls == expected_calls
        assert index.read_consolidated_notice_cover(db, own) == old_own
        if phase == "revoked":
            assert outcome == ("error" if mode == "fetch" else "transient_failure")
            assert read_notice_cover(db, source.document) == source
            assert index.read_consolidated_notice_cover(db, before.document) == before
            assert len(jobs(db)) == 1
            return
        assert outcome == "current"
        latest = read_notice_cover(db, source.document)
        assert latest.epoch == source.epoch + 1
        assert journal.read_job(db, job_ref).state == "current"
        group_job, = (job for job in jobs(db) if job.spec.implementation_digest == digest)
        assert group_job.state == "pending"
        if before is not None:
            assert group_job.spec.target == before.document.source_key
            assert index.read_consolidated_notice_cover(db, before.document) == before
        if old_job is not None:
            assert group_job.ref.key == old_job.ref.key
            assert group_job.ref.generation == old_job.ref.generation + 1
        own_job, = (job for job in jobs(db) if job.spec.target == own.source_key)
        assert own_job.state == "pending"
    finally:
        tasks.close()
        if authority is not None:
            authority.close()
        db.close()

    # No parser or transport grant required by the restarted aggregate worker.
    db = Storage(path)
    tasks = broker()
    try:
        pump = PublicNoticeWorkPump(db, tasks, clock=lambda: now)
        assert pump._bridge.submit(own_job.ref).result(timeout=10) == "current"
        group_job, = (job for job in jobs(db) if job.spec.implementation_digest == digest)
        assert pump._bridge.submit(group_job.ref).result(timeout=10) == "current"
        with db._lock, db._connect() as sql:
            sql.execute("BEGIN")
            document, *_ = initial._group_inputs(sql, source.document.source_key)
        result = index.read_consolidated_notice_cover(db, document)
        assert result.document.source_key == group_job.spec.target
        assert {member.source_ref: member.source_epoch for member in result.sources} == {
            item.document.source_key: latest.epoch if item == source else item.epoch
            for item in sources
        }
        updated, = (member for member in result.sources if member.source_ref == source.document.source_key)
        assert updated.public_bibliography.description == "Résumé corrigé par le producteur"
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        tasks.close()
        db.close()
