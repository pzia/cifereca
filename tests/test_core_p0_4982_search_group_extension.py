"""#4982 Search recovery for strict extension of an already proven public C."""
from threading import Event

from cifereca.core import notice_c_job_journal as journal
from cifereca.core import notice_c_cover_index as index
from cifereca.core import notice_c_group_batch_publication as producer
from cifereca.core import notice_c_origin_relink as relink
from cifereca.core import notice_c_initial_group_publication as recovery
from cifereca.core import search_orchestration as search
from cifereca.task_admission import SharedTaskAdmission
from tests.test_core_p0_4982_notice_work_runtime import open_core
from tests.test_core_p0_4982_relink_inputs import group, add_third
from tests.test_core_p0_4982_search_repair import _admitted


def _budget():
    return SharedTaskAdmission(max_inflight=4, reserve_user_slots=1)


def test_failed_live_extension_reuses_existing_c_and_relink_port(tmp_path, monkeypatch):
    with open_core(tmp_path, notice_work_admission=_budget()) as core:
        ref, pair = group(core.storage, tmp_path)
        old = index.read_consolidated_notice_cover(core.storage, ref)
        third = add_third(core.storage, tmp_path)
        row = _admitted("L'ouvrage commun", "edition-c", source_provider="seller-c",
                        thumbnail_url="https://covers.example.org/third.jpg")
        done = Event()
        events = []
        def observe(document, epoch):
            events.append((document, epoch))
            done.set()
        core.notice_work.bind_committed_observer(observe)
        def fail(*args, **kwargs):
            raise OSError("simulated failed live group extension")
        with monkeypatch.context() as patch:
            patch.setattr(producer, "_persist_origin_linked_public_cover_group_txn", fail)
            pairs = search._ingest_public_search_c(
                core.library, [row], identity_id="A", now=101,
                require_public_cover_witness=True,
            )
        (_, current, own), = pairs
        assert own is not None and own.document != ref
        assert current.document == third.document
        assert done.wait(10)
        latest = index.read_consolidated_notice_cover(core.storage, ref)
        assert latest.epoch == old.epoch + 1
        assert {m.source_ref for m in latest.sources} == {
            s.document.source_key for s in (*pair, third)
        }
        assert events == [(ref, latest.epoch)]
        with core.storage._lock, core.storage._connect() as db:
            keys = db.execute("SELECT key FROM metadata WHERE key>=? AND key<?",
                              (journal._PREFIX, journal._END)).fetchall()
            assert len(keys) == 1
            job = journal._load(db, keys[0][0][len(journal._PREFIX):])
        assert job.state == "current" and job.attempts == 1
        assert job.spec.kind == "relink_notices"
        assert job.spec.implementation_digest == relink.IMPLEMENTATION_DIGEST
        assert job.spec.target == ref.source_key and job.spec.source_ref is None
        assert recovery.request_search_group_repair(core.storage, current, now=102) is None
        assert core.storage.entitlements() == [] and core.storage.acquisitions() == []


def test_extension_recovery_dedup_and_live_origin_revocation(tmp_path):
    with open_core(tmp_path, notice_work_admission=_budget()) as core:
        ref, pair = group(core.storage, tmp_path)
        old = index.read_consolidated_notice_cover(core.storage, ref)
        third = add_third(core.storage, tmp_path)
        index.persist_exact_public_search_cover(core.storage, third)
        job = recovery.request_search_group_repair(core.storage, third, now=100)
        assert job is not None
        assert recovery.request_search_group_repair(core.storage, pair[0], now=101) == job
        assert core.brokers["tasks"]._executor is None
        with core.storage._lock, core.storage._connect() as db:
            db.execute(
                "DELETE FROM origin_links WHERE source_reference_id IN ("
                "SELECT source_reference_id FROM source_references WHERE provider_id='seller-c')"
            )
        events = []
        core.notice_work.bind_committed_observer(lambda *args: events.append(args))
        worker, = core.notice_work.request_tick().result(timeout=10).handles
        assert worker.result(timeout=10) == "transient_failure"
        assert journal.read_job(core.storage, job).state == "pending"
        assert index.read_consolidated_notice_cover(core.storage, ref) == old
        assert events == []
