"""#4982 first self-C creation recovered from actual admitted SQLite source."""
from threading import Event

import pytest

from cifereca.core import notice_c_job_journal as journal
from cifereca.core import notice_c_cover_index as index
from cifereca.core import notice_c_initial_publication as initial
from cifereca.core import search_orchestration as search
from cifereca.task_admission import SharedTaskAdmission
from tests.test_core_p0_4982_notice_work_runtime import open_core
from tests.test_core_p0_4982_exact_public_recompute import source
from tests.test_core_p0_6366_public_c_sqlite_source_search import verified_public


def _budget():
    return SharedTaskAdmission(max_inflight=4, reserve_user_slots=1)


def test_initial_request_survives_restart_and_deduplicates(tmp_path):
    with open_core(tmp_path, notice_work_admission=_budget()) as core:
        persisted = source(core.storage, "Un titre", 100)
        ref = initial.request_initial_publication(core.storage, persisted, now=100)
        assert initial.request_initial_publication(core.storage, persisted, now=101) == ref
        job = journal.read_job(core.storage, ref)
        assert job.spec.source_ref == persisted.document.source_key
        document = index.canonical_cover_document_ref(persisted.observation.key.edition_id)
        assert job.spec.target == document.source_key
        assert index.read_consolidated_notice_cover(core.storage, document) is None
        assert core.brokers["tasks"]._executor is None
    with open_core(tmp_path, notice_work_admission=_budget()) as core:
        events = []
        core.notice_work.bind_committed_observer(lambda doc, epoch: events.append((doc, epoch)))
        worker, = core.notice_work.request_tick().result(timeout=10).handles
        assert worker.result(timeout=10) == "current"
        c = index.read_consolidated_notice_cover(core.storage, document)
        assert c.epoch == 1 and c.sources[0].public_bibliography.title == "Un titre"
        assert events == [(document, 1)]
        assert journal.read_job(core.storage, ref).state == "current"
        assert initial.request_initial_publication(core.storage, persisted, now=102) is None
        assert core.storage.entitlements() == [] and core.storage.acquisitions() == []


def test_initial_request_does_not_overwrite_racing_live_publication(tmp_path):
    with open_core(tmp_path, notice_work_admission=_budget()) as core:
        persisted = source(core.storage, "Un titre", 100)
        ref = initial.request_initial_publication(core.storage, persisted, now=100)
        c = index.persist_exact_public_search_cover(core.storage, persisted)
        events = []
        core.notice_work.bind_committed_observer(lambda *args: events.append(args))
        worker, = core.notice_work.request_tick().result(timeout=10).handles
        assert worker.result(timeout=10) == "current"
        assert index.read_consolidated_notice_cover(core.storage, c.document) == c
        assert not events and journal.read_job(core.storage, ref).state == "current"


def test_first_live_search_failure_requests_initial_publication(tmp_path, monkeypatch):
    with open_core(tmp_path, notice_work_admission=_budget()) as core:
        row = verified_public(title="Première notice")
        row._core_same_call_public_cover_witness = (
            search._CORE_PUBLIC_COVER_WITNESS, search._public_cover_witness_coordinates(row),
        )
        assert search._verified_public_cover_witness(row)
        done = Event()
        events = []
        def observe(document, epoch):
            events.append((document, epoch))
            done.set()
        core.notice_work.bind_committed_observer(observe)
        def fail(*args, **kwargs):
            raise OSError("simulated first live commit failure")
        with monkeypatch.context() as patch:
            patch.setattr(index, "_persist_exact_public_search_cover_txn", fail)
            pairs = search._ingest_public_search_c(
                core.library, [row], identity_id="alice", now=100,
                require_public_cover_witness=True,
            )
        (_row, persisted, canonical), = pairs
        assert canonical is None and persisted is not None
        assert done.wait(10)
        document = index.canonical_cover_document_ref(persisted.observation.key.edition_id)
        assert events == [(document, 1)]
        assert index.read_consolidated_notice_cover(core.storage, document).sources[0].public_bibliography.title == "Première notice"
        assert not hasattr(row, "_core_notice_canonical_cover_document_ref")
        assert core.storage.entitlements() == [] and core.storage.acquisitions() == []


def test_initial_source_cannot_be_used_by_other_implementation(tmp_path):
    with open_core(tmp_path) as core:
        persisted = source(core.storage, "Un titre", 100)
        doc = index.canonical_cover_document_ref(persisted.observation.key.edition_id)
        with pytest.raises(ValueError, match="invalid_initial_source"):
            journal.public_job_spec(
                "recompute_aggregate", doc, input_digest="a" * 64,
                implementation_digest="b" * 64,
                source_ref=persisted.document.source_key,
            )
