"""Group savepoint and exact durable repair using real public C and OriginLinks."""
import pytest

from cifereca.core import notice_c_group_batch_publication as batch
from cifereca.core import notice_c_cover_index as index
from cifereca.core import notice_c_job_journal as journal
from cifereca.core import notice_c_origin_recompute as recompute
from cifereca.core import notice_c_origin_relink as relink
from cifereca.core import notice_c_initial_group_publication as initial
from tests.test_core_p0_4982_initial_group_publication import _budget, _seed
from tests.test_core_p0_4982_notice_work_runtime import open_core
from tests.test_core_p0_4982_relink_inputs import group, add_third
from tests.test_core_p0_6366_public_c_sqlite_source_search import persist, verified_public
from tests.test_core_p0_6366_library_recent_public_c import A_URL


def _publish(core, sources):
    with core.storage._lock, core.storage._connect() as db:
        db.execute("BEGIN IMMEDIATE")
        return batch.publish_search_groups_txn(db, sources, now=101)


def _fail_after_write(monkeypatch):
    original = batch._persist_origin_linked_public_cover_group_txn
    def fail(*args, **kwargs):
        original(*args, **kwargs)
        raise OSError("simulated failure before the group savepoint commits")
    monkeypatch.setattr(batch, "_persist_origin_linked_public_cover_group_txn", fail)


def _run(core, ref, implementation):
    job = journal.read_job(core.storage, ref)
    assert job.spec.implementation_digest == implementation
    assert job.state == "pending"
    worker, = core.notice_work.request_tick().result(timeout=10).handles
    assert worker.result(timeout=10) == "current"
    assert journal.read_job(core.storage, ref).state == "current"


def test_success_publishes_one_group_without_request(tmp_path):
    with open_core(tmp_path, notice_work_admission=_budget()) as core:
        sources = _seed(core, tmp_path)
        linked, requests = _publish(core, sources)
        assert requests == ()
        assert set(linked) == {source.document.source_key for source in sources}
        first = next(iter(linked.values()))
        assert all(c == first for c in linked.values()) and len(first.sources) == 2
        assert _publish(core, sources) == (linked, ())
        assert core.brokers["tasks"]._executor is None


def test_initial_writer_failure_rolls_back_partial_group_and_requests_once(tmp_path, monkeypatch):
    with open_core(tmp_path, notice_work_admission=_budget()) as core:
        sources = _seed(core, tmp_path)
        with monkeypatch.context() as patch:
            _fail_after_write(patch)
            linked, requests = _publish(core, sources)
            assert not linked
            ref, = requests
            assert _publish(core, sources) == ({}, requests)
        with core.storage._lock, core.storage._connect() as db:
            assert db.execute("SELECT count(*) FROM metadata WHERE key>=? AND key<?",
                              (index._PREFIX, index._PREFIX + "\uffff")).fetchone()[0] == 2
        assert core.brokers["tasks"]._executor is None
    with open_core(tmp_path, notice_work_admission=_budget()) as core:
        _run(core, ref, initial.IMPLEMENTATION_DIGEST)
        assert core.storage.entitlements() == [] and core.storage.acquisitions() == []


def test_extension_failure_reuses_exact_relink_target(tmp_path, monkeypatch):
    with open_core(tmp_path, notice_work_admission=_budget()) as core:
        document, pair = group(core.storage, tmp_path)
        old = index.read_consolidated_notice_cover(core.storage, document)
        third = add_third(core.storage, tmp_path)
        index.persist_exact_public_search_cover(core.storage, third)
        with monkeypatch.context() as patch:
            _fail_after_write(patch)
            linked, (ref,) = _publish(core, (third,))
        assert not linked and index.read_consolidated_notice_cover(core.storage, document) == old
        _run(core, ref, relink.IMPLEMENTATION_DIGEST)
        latest = index.read_consolidated_notice_cover(core.storage, document)
        assert {m.source_ref for m in latest.sources} == {
            s.document.source_key for s in (*pair, third)
        }


def test_existing_dirty_group_failure_reuses_recompute_but_noop_does_not_queue(tmp_path, monkeypatch):
    with open_core(tmp_path, notice_work_admission=_budget()) as core:
        sources = _seed(core, tmp_path)
        linked, _ = _publish(core, sources)
        old = next(iter(linked.values()))
        with monkeypatch.context() as patch:
            _fail_after_write(patch)
            assert _publish(core, sources) == ({}, ())
        row = verified_public(source_provider="seller-a", record_id="edition-a",
                              title="Un ouvrage commun", author="Autrice",
                              description="Nouveau résumé", thumbnail_url=A_URL)
        changed = persist(core.storage, row, at=101)
        index.persist_exact_public_search_cover(core.storage, changed)
        with monkeypatch.context() as patch:
            _fail_after_write(patch)
            linked, (ref,) = _publish(core, (changed,))
        assert not linked and index.read_consolidated_notice_cover(core.storage, old.document) == old
        _run(core, ref, recompute.IMPLEMENTATION_DIGEST)
        latest = index.read_consolidated_notice_cover(core.storage, old.document)
        assert any(m.public_bibliography.description == "Nouveau résumé" for m in latest.sources)


def test_group_batch_requires_caller_transaction(tmp_path):
    with open_core(tmp_path, notice_work_admission=_budget()) as core:
        with core.storage._lock, core.storage._connect() as db:
            with pytest.raises(ValueError, match="transaction_required"):
                batch.publish_search_groups_txn(db, (), now=101)
