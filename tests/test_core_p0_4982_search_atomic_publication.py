"""Search source commits with its exact C or durable repair, on real SQLite."""
import sqlite3

import pytest

from cifereca.core import notice_c_cover_index as index
from cifereca.core import notice_c_initial_publication as initial
from cifereca.core import notice_c_job_journal as journal
from cifereca.core import search_orchestration as search
from cifereca.core.notice_c_public_search import search_public_notice_c
from cifereca.core.notice_cover_repository import read_notice_cover
from cifereca.task_admission import SharedTaskAdmission
from tests.test_core_p0_4982_notice_work_runtime import open_core
from tests.test_core_p0_4982_search_repair import _admitted


class Interrupted(BaseException):
    """Stop the producer after commit, before its in-process recovery hint."""


def _budget():
    return SharedTaskAdmission(max_inflight=4, reserve_user_slots=1)


def _jobs(storage):
    with storage._lock, storage._connect() as db:
        return [journal._load(db, key[len(journal._PREFIX):]) for key, in db.execute(
            "SELECT key FROM metadata WHERE key>=? AND key<? ORDER BY key",
            (journal._PREFIX, journal._END),
        )]


def _ingest(core, rows, now=100):
    return search._ingest_public_search_c(
        core.library, rows, identity_id="alice", now=now,
        require_public_cover_witness=True,
    )


@pytest.mark.parametrize("existing", [False, True])
def test_commit_before_interruption_retains_repair_and_restarts(tmp_path, monkeypatch, existing):
    with open_core(tmp_path, notice_work_admission=_budget()) as core:
        old = _ingest(core, [_admitted("Ancien titre")])[0][2] if existing else None
        assert _jobs(core.storage) == []
        assert core.brokers["tasks"]._executor is None
        original = index._persist_exact_public_search_cover_txn

        def fail_after_write(db, source, **kwargs):
            original(db, source, **kwargs)
            raise OSError("failure after canonical write, before commit")

        def interrupted(*args, **kwargs):
            raise Interrupted()

        row = _admitted("Nouveau titre")
        with monkeypatch.context() as patch:
            patch.setattr(index, "_persist_exact_public_search_cover_txn", fail_after_write)
            patch.setattr(search, "_reconcile_progressive_public_c", interrupted)
            with pytest.raises(Interrupted):
                _ingest(core, [row], now=101)
        source_document = row._core_notice_cover_document_ref
        job, = _jobs(core.storage)
        source = read_notice_cover(core.storage, source_document)
        document = index.canonical_cover_document_ref(source.observation.key.edition_id)
        assert job.spec.target == document.source_key
        assert index.read_consolidated_notice_cover(core.storage, document) == old
        assert job.state == "pending" and job.attempts == 0
        assert core.brokers["tasks"]._executor is None
        assert not hasattr(row, "_core_notice_canonical_cover_document_ref")
    with open_core(tmp_path, notice_work_admission=_budget()) as core:
        scan = core.notice_work.request_tick().result(timeout=10)
        worker, = scan.handles
        assert worker.result(timeout=10) == "current"
        current = index.read_consolidated_notice_cover(core.storage, document)
        assert current.sources[0].public_bibliography.title == "Nouveau titre"
        assert current.sources[0].source_ref == source_document.source_key
        assert journal.read_job(core.storage, job.ref).state == "current"
        assert core.storage.entitlements() == [] and core.storage.acquisitions() == []


def test_journal_failure_rolls_back_only_failed_source_and_its_fence(tmp_path, monkeypatch):
    from cifereca.core import notice_cover_repository as repository
    with open_core(tmp_path, notice_work_admission=_budget()) as core:
        original = index._persist_exact_public_search_cover_txn

        def fail_one(db, source, **kwargs):
            if source.observation.fields_copy()["search_result"]["public_bibliography"]["title"] == "Refusée":
                raise OSError("canonical unavailable")
            return original(db, source, **kwargs)

        def journal_failure(*args, **kwargs):
            raise sqlite3.OperationalError("journal unavailable")

        with monkeypatch.context() as patch:
            patch.setattr(index, "_persist_exact_public_search_cover_txn", fail_one)
            patch.setattr(initial, "request_initial_publication_txn", journal_failure)
            rejected = _admitted("Refusée", "rejected")
            accepted = _admitted("Conservée", "accepted")
            pairs = _ingest(core, [rejected, accepted])
        assert len(pairs) == 1 and pairs[0][0] is accepted
        assert pairs[0][2] is not None
        assert not hasattr(rejected, "_core_notice_cover_document_ref")
        assert _jobs(core.storage) == []
        with core.storage._lock, core.storage._connect() as db:
            assert db.execute("SELECT count(*) FROM metadata WHERE key LIKE ?",
                              (repository._PREFIX + "%",)).fetchone()[0] == 1
        assert search_public_notice_c(core.storage, "refusée", now=100).hits == ()
        # The failed attempt left no fence; the exact same timestamp may retry.
        assert _ingest(core, [rejected])[0][1].epoch == 1


def test_successful_opt_in_search_needs_no_job_or_dispatch(tmp_path):
    with open_core(tmp_path, notice_work_admission=_budget()) as core:
        first = _ingest(core, [_admitted("Un titre")])[0]
        same = _ingest(core, [_admitted("Un titre")], now=101)[0]
        assert first[1].epoch == same[1].epoch
        assert first[2].epoch == same[2].epoch
        assert _jobs(core.storage) == []
        assert core.brokers["tasks"]._executor is None
