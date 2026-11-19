"""Actual opt-in Search: source, public group and repair intent share commit."""
from cifereca.core import notice_c_group_batch_publication as batch
from cifereca.core import notice_c_cover_index as index
from cifereca.core import notice_c_job_journal as journal
from cifereca.core import notice_cover_repository as repository
from cifereca.core import notice_c_origin_link_producer as legacy
from cifereca.core import search_orchestration as search
from tests.test_core_p0_4982_initial_group_publication import _budget
from tests.test_core_p0_4982_notice_work_runtime import open_core
from tests.test_core_p0_4982_search_repair import _admitted
from tests.test_core_p0_6366_library_recent_public_c import _real_origins, A_URL, B_URL


def _rows():
    return [
        _admitted("Même titre", "edition-a", source_provider="seller-a", thumbnail_url=A_URL),
        _admitted("Même titre", "edition-b", source_provider="seller-b", thumbnail_url=B_URL),
    ]


def _ingest(core, rows, now=100):
    return search._ingest_public_search_c(
        core.library, rows, identity_id="alice", now=now,
        require_public_cover_witness=True,
    )


def _job_refs(storage):
    with storage._lock, storage._connect() as db:
        keys = db.execute("SELECT key FROM metadata WHERE key>=? AND key<?",
                          (journal._PREFIX, journal._END)).fetchall()
        return tuple(journal._load(db, key[0][len(journal._PREFIX):]).ref for key in keys)


def _forbid(*args, **kwargs):
    raise AssertionError("unexpected second writer or post-commit repair request")


def test_search_group_success_and_progressive_rebinding_never_write_twice(tmp_path, monkeypatch):
    with open_core(tmp_path, notice_work_admission=_budget()) as core:
        _real_origins(core.storage, tmp_path)
        a, b = _rows()
        original = batch._persist_origin_linked_public_cover_group_txn
        calls = []
        def counted(db, **kwargs):
            assert db.in_transaction
            calls.append(kwargs["expected_document"])
            return original(db, **kwargs)
        monkeypatch.setattr(batch, "_persist_origin_linked_public_cover_group_txn", counted)
        monkeypatch.setattr(legacy, "persist_origin_linked_public_cover_group", _forbid)
        monkeypatch.setattr(core.library, "_notice_work_recover_search", _forbid)
        first = _ingest(core, [a])
        second = _ingest(core, [b])
        assert len(first) == len(second) == 1
        own = first[0][2].document
        document, = calls
        assert document != own
        search._reconcile_progressive_public_c(
            core.storage, first + second, restore_own=True, read_only=True,
        )
        assert calls == [document]
        assert all(row._core_notice_canonical_cover_document_ref == document for row in (a, b))
        assert _job_refs(core.storage) == ()
        assert core.brokers["tasks"]._executor is None


def test_repair_is_durable_before_wake_and_survives_restart(tmp_path, monkeypatch):
    with open_core(tmp_path, notice_work_admission=_budget()) as core:
        _real_origins(core.storage, tmp_path)
        def fail(*args, **kwargs):
            raise OSError("simulated live group write failure")
        wakes = []
        def suppress_wake():
            # The real journal and both source rows already committed here.
            wakes.extend(_job_refs(core.storage))
        with monkeypatch.context() as patch:
            patch.setattr(batch, "_persist_origin_linked_public_cover_group_txn", fail)
            patch.setattr(core.library, "_notice_work_search_committed", suppress_wake)
            patch.setattr(core.library, "_notice_work_recover_search", _forbid)
            rows = _rows()
            pairs = _ingest(core, rows)
        assert len(pairs) == 2
        ref, = wakes
        assert _job_refs(core.storage) == (ref,)
        job = journal.read_job(core.storage, ref)
        assert job.state == "pending" and job.attempts == 0
        for _, source, own in pairs:
            assert repository.read_notice_cover(core.storage, source.document) == source
            assert own is not None and own.document.source_key != job.spec.target
        assert core.brokers["tasks"]._executor is None
    with open_core(tmp_path, notice_work_admission=_budget()) as core:
        worker, = core.notice_work.request_tick().result(timeout=10).handles
        assert worker.result(timeout=10) == "current"
        assert journal.read_job(core.storage, ref).state == "current"
        assert core.storage.entitlements() == [] and core.storage.acquisitions() == []


def test_refused_group_intent_rolls_back_chunk_not_later_independent_chunk(tmp_path, monkeypatch):
    with open_core(tmp_path, notice_work_admission=_budget()) as core:
        _real_origins(core.storage, tmp_path)
        monkeypatch.setattr(repository, "_BATCH_LIMIT", 2)
        def fail(*args, **kwargs):
            raise OSError("simulated live group write failure")
        def refuse(*args, **kwargs):
            raise ValueError("notice_work_capacity")
        monkeypatch.setattr(batch, "_persist_origin_linked_public_cover_group_txn", fail)
        monkeypatch.setattr(batch, "request_search_group_repair_txn", refuse)
        monkeypatch.setattr(core.library, "_notice_work_search_committed", _forbid)
        a, b = _rows()
        unrelated = _admitted("Témoin indépendant", "unrelated")
        (row, source, own), = _ingest(core, [a, b, unrelated])
        assert row is unrelated and own is not None
        assert _job_refs(core.storage) == ()
        assert all(not hasattr(item, "_core_notice_cover_document_ref") for item in (a, b))
        with core.storage._lock, core.storage._connect() as db:
            for prefix in (repository._PREFIX, repository._FENCE_PREFIX, index._PREFIX):
                count = db.execute("SELECT count(*) FROM metadata WHERE key>=? AND key<?",
                                   (prefix, prefix + "\uffff")).fetchone()[0]
                assert count == 1
        assert repository.read_notice_cover(core.storage, source.document) == source
        assert core.brokers["tasks"]._executor is None
