"""#6939: one runtime drains public C and private purchase enrichment.

Real Sync, SQLite, journal, heartbeat, aggregate and purchase publication.
Synthetic provider ports only; no remote network or GTK latency claim.
"""
from contextlib import contextmanager, nullcontext
from threading import Event, RLock, get_ident
from time import monotonic, time
from types import SimpleNamespace

import pytest

from cifereca.core import notice_c_job_journal as journal
from cifereca.core.notice_c_cover_index import (
    persist_exact_public_search_cover, read_consolidated_notice_cover,
)
from cifereca.core.notice_c_exact_recompute import request_exact_public_recompute
from cifereca.core.notice_c_public_search import search_public_notice_c
from cifereca.core.notice_c_purchase_runtime import install_purchase_runtime
from cifereca.core.notice_c_purchase_settings import read_purchase_policy, change_purchase_policy
from cifereca.core.notice_c_work_runtime import NoticeWorkRuntime
from cifereca.core.sync_coordination import synchronize_account_source
from cifereca.plugin_api import ProviderSearchResponse, PublicSearchNotice
from cifereca.task_admission import SharedTaskAdmission
from cifereca.task_queue import TaskPriority
from cifereca.task_runtime import TaskBrokerImpl
from tests.test_core_p0_3171_canonical_sync_stage import rig, refs
from tests.test_core_p0_1072_purchase_enrichment_same_read_admission import install
from tests.test_core_p0_6366_public_c_sqlite_source_search import persist, verified_public
from tests.test_core_p0_6366_real_search_notice_c_ingestion import public_row


@pytest.mark.parametrize("public_count", [0, 1, 3])
@pytest.mark.parametrize("start_disabled", [False, True])
def test_shared_runtime_drains_both_lanes_after_admission_pressure(rig, public_count, start_disabled):
    service, db, root = rig
    service._source_context_lock = RLock()
    reads = install(service)
    viewer = get_ident()
    transfers = []
    events = []
    public_id = "seller-a-catalog"
    service._provider_enabled_for_identity = lambda provider, owner: (
        provider == public_id and owner == "A"
    )
    service.session.purchase_enrichment_public_provider_id = public_id

    @contextmanager
    def source_guard():
        yield True

    service.session.capture_purchase_enrichment_guard = lambda account: source_guard
    public = SimpleNamespace(
        provider_id=public_id, credential_id="__public__", credentials={},
        _accounts=[], connected=True, purchase_enrichment_source_provider_id="seller-a",
    )

    def response(url, title, *, check_authority, control):
        assert get_ident() != viewer
        assert title == "Titre synthétique"
        assert len(refs(db)[0]) == 1
        assert check_authority() is True
        with check_authority.hold_transport_start():
            transfers.append(url)
        row = public_row(
            source_provider=public_id, title=title, friendly_url=url, thumbnail_url=None,
            metadata={"public_url_evidence": public_id + ".public_search_product_card"},
        )
        return ProviderSearchResponse(
            results=[row], public_notices=(PublicSearchNotice(result=row),),
        ), int(time()), nullcontext

    public.purchase_enrichment_public_response = response
    service._sessions[service._session_key(public_id, "__public__")] = public
    budget = SharedTaskAdmission(max_inflight=4, reserve_user_slots=1)
    tasks = TaskBrokerImpl(object(), max_workers=3, shared_admission=budget)
    runtime = NoticeWorkRuntime(db, tasks)

    def idle():
        deadline = monotonic() + 10
        while monotonic() < deadline:
            if not budget.snapshot()["inflight"] and runtime._scan is None:
                return
            Event().wait(.005)
        pytest.fail("shared broker did not release its reservations")

    try:
        executor = install_purchase_runtime(service, runtime)
        if start_disabled:
            off = change_purchase_policy(
                service, expected_storage=db, expected=read_purchase_policy(db), enabled=False,
            )
            assert not off.enabled
        public_jobs = []
        for i in range(public_count):
            def observed(title, at):
                return persist(db, verified_public(
                    source_provider="supplier-b", record_id=f"exact-{i}",
                    title=title,
                ), at=at)
            initial = persist_exact_public_search_cover(db, observed("Ancien", 10))
            observed("Nouveau", 11)
            ref = request_exact_public_recompute(
                db, initial.document, now=100, expected_generation=0,
            )
            public_jobs.append((ref, initial))

        def committed(document, epoch):
            assert get_ident() != viewer
            current = read_consolidated_notice_cover(db, document)
            events.append((document, epoch, current.epoch))

        assert runtime.bind_committed_observer(committed)
        held = [budget.acquire(priority=int(TaskPriority.METADATA)) for _ in range(3)]
        try:
            assert all(lease is not None for lease in held)
            if start_disabled:
                # Consent OFF does not block the existing inventory commit
                # or erase independently requested public recomputations.
                disabled = synchronize_account_source(service, service.account)["canonical_rights"]
                assert "purchase_enrichment" not in disabled
                assert len(refs(db)[0]) == 1 and reads == [] and transfers == []
                assert executor.ready_refs() == ()
                assert all(journal.read_job(db, ref).attempts == 0 for ref, _ in public_jobs)
                before_enable = runtime.documentary_revision()
                on = change_purchase_policy(service, expected_storage=db, expected=off, enabled=True)
                assert on.enabled and on.revision == off.revision + 1
                # Consent does not itself call the provider or regenerate
                # a grant. Only a new qualified same-read Sync can do so.
                assert runtime.documentary_revision() == before_enable
                assert executor.ready_refs() == () and transfers == [] and reads == []
            summary = synchronize_account_source(service, service.account)["canonical_rights"]
            assert summary["purchase_enrichment"] == {
                "requested": 1, "admitted": 1, "deferred": 0, "unrequested": 0,
            }
            purchase, = executor.ready_refs()
            all_refs = [ref for ref, _ in public_jobs] + [purchase]
            assert all(journal.read_job(db, ref).attempts == 0 for ref in all_refs)
            assert runtime.request_tick() is None
            assert transfers == [] and events == []
            assert tasks.submit(lambda control: "interactive",
                                priority=TaskPriority.USER_ACTION).result(timeout=10) == "interactive"
        finally:
            for lease in held:
                if lease is not None:
                    lease.release()
        idle()

        handles_seen = 0
        for _ in range(16):
            scan = runtime.request_tick()
            assert scan is not None
            tick = scan.result(timeout=10)
            assert tick.examined <= 32 and len(tick.handles) <= 2
            for handle in tick.handles:
                assert handle.result(timeout=10) == "current"
                handles_seen += 1
            idle()
            if all(journal.read_job(db, ref).state == "current" for ref in all_refs):
                break
        assert handles_seen == public_count + 1
        assert all(journal.read_job(db, ref).state == "current"
                   and journal.read_job(db, ref).attempts == 1 for ref in all_refs)
        assert set(events) == {(old.document, old.epoch + 1, old.epoch + 1)
                               for _, old in public_jobs}
        assert len(events) == public_count
        assert transfers == ["https://catalog.example.org/product/123"]
        assert reads == ["same-read"]
        assert len(search_public_notice_c(db, "Nouveau").hits) == public_count
        assert len(search_public_notice_c(db, "synthétique").hits) == 1
        assert executor.ready_refs() == ()

        # The purchase worker commits the exact source, association and
        # separate durable initial-publication intent in one transaction.
        # Source-search visibility alone does NOT mean canonical C exists.
        ready = journal.read_ready_page(
            db, now=int(time()),
            supported=(("recompute_aggregate", journal._INITIAL_PUBLIC_C_DIGEST),),
        )
        initial_ref, = ready.refs
        initial_job = journal.read_job(db, initial_ref)
        assert initial_job.state == "pending" and initial_job.attempts == 0
        assert initial_job.spec.source_ref
        for _ in range(16):
            tick = runtime.request_tick().result(timeout=10)
            assert tick.examined <= 32 and len(tick.handles) <= 2
            for handle in tick.handles:
                assert handle.result(timeout=10) == "current"
                handles_seen += 1
            idle()
            if journal.read_job(db, initial_ref).state == "current":
                break
        initial_job = journal.read_job(db, initial_ref)
        assert initial_job.state == "current" and initial_job.attempts == 1
        assert handles_seen == public_count + 2
        purchase_hit, = search_public_notice_c(db, "synthétique").hits
        assert purchase_hit.document.source_key == initial_job.spec.target
        canonical = read_consolidated_notice_cover(db, purchase_hit.document)
        assert canonical is not None and len(canonical.sources) == 1
        assert canonical.sources[0].source_ref == initial_job.spec.source_ref
        assert set(events) == ({(old.document, old.epoch + 1, old.epoch + 1)
                                for _, old in public_jobs}
                               | {(canonical.document, canonical.epoch, canonical.epoch)})
        assert len(events) == public_count + 1
        assert transfers == ["https://catalog.example.org/product/123"]
        from cifereca.core.notice_c_purchase_association import read_purchase_notice_association
        from cifereca.plugin_api import from_legacy_rights_snapshot
        resource, = from_legacy_rights_snapshot(service.snapshot).resources
        association = read_purchase_notice_association(db, resource.source, identity_id="A")
        assert association is not None and association.document == canonical.document

        # Only after both lanes AND the dependent C publication have drained
        # may an identical same-read inventory claim a silent replay.
        before = runtime.documentary_revision()
        synchronize_account_source(service, service.account)
        assert reads == ["same-read", "same-read"]
        idle()
        assert runtime.request_tick().result(timeout=10).handles == ()
        idle()
        assert runtime.documentary_revision() == before
        assert len(transfers) == 1 and len(events) == public_count + 1
        assert journal.read_job(db, initial_ref) == initial_job
        assert read_consolidated_notice_cover(db, canonical.document) == canonical
        assert db.entitlements() == [] and db.acquisitions() == [] and db.placements() == []
        assert len(refs(db)[0]) == 1 and refs(db)[1] == 0 and not root.exists()
    finally:
        # TaskBrokerImpl.close is deliberately nonblocking. The borrowed rig
        # closes SQLite during fixture teardown, even after an assertion fails.
        # Capture the owned executor before close detaches it, then join every
        # cancelled/active worker before returning the storage to the fixture.
        pool = tasks._executor
        runtime.close()
        tasks.close()
        if pool is not None:
            pool.shutdown(wait=True, cancel_futures=True)
