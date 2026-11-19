"""Bounded local salvage of independent rows after qualified group refusal."""
import sqlite3

from cifereca.core import notice_c_cover_index as index
from cifereca.core import notice_c_group_batch_publication as batch
from cifereca.core import notice_cover_repository as repository
from tests.test_core_p0_4982_initial_group_publication import _budget
from tests.test_core_p0_4982_notice_work_runtime import open_core
from tests.test_core_p0_4982_search_atomic_groups import _rows, _ingest, _job_refs, _forbid
from tests.test_core_p0_4982_search_repair import _admitted
from tests.test_core_p0_6366_library_recent_public_c import _real_origins


def test_same_chunk_salvages_only_independent_row_after_group_intent_refusal(tmp_path, monkeypatch):
    with open_core(tmp_path, notice_work_admission=_budget()) as core:
        _real_origins(core.storage, tmp_path)
        a, b = _rows()
        unrelated = _admitted("Témoin indépendant", "unrelated")
        original = batch.request_search_group_repair_txn
        captured = []

        def fail_group(*args, **kwargs):
            raise OSError("simulated live group failure")

        def refuse_after_request(*args, **kwargs):
            ref = original(*args, **kwargs)
            assert ref is not None
            captured.append(ref)
            raise ValueError("simulated journal capacity refusal")

        writer = index._persist_exact_public_search_cover_txn
        independent_attempts = []
        def self_write(db, source, **kwargs):
            if source.observation.fields_copy()["search_result"]["public_bibliography"]["title"] == unrelated.title:
                independent_attempts.append(source.epoch)
                if len(independent_attempts) == 1:
                    # This self repair hint must not survive the first
                    # rollback if the independent replay succeeds normally.
                    raise OSError("simulated transient own C failure")
            return writer(db, source, **kwargs)

        monkeypatch.setattr(repository, "_BATCH_LIMIT", 3)
        monkeypatch.setattr(batch, "_persist_origin_linked_public_cover_group_txn", fail_group)
        monkeypatch.setattr(batch, "request_search_group_repair_txn", refuse_after_request)
        monkeypatch.setattr(index, "_persist_exact_public_search_cover_txn", self_write)
        monkeypatch.setattr(core.library, "_notice_work_search_committed", _forbid)
        (row, source, own), = _ingest(core, [a, b, unrelated])
        assert row is unrelated and source.epoch == 1 and own is not None
        assert independent_attempts == [1, 1] and len(captured) == 1
        assert _job_refs(core.storage) == ()
        assert all(not hasattr(item, "_core_notice_cover_document_ref") for item in (a, b))
        with core.storage._lock, core.storage._connect() as db:
            for prefix in (repository._PREFIX, repository._FENCE_PREFIX, index._PREFIX):
                count = db.execute("SELECT count(*) FROM metadata WHERE key>=? AND key<?",
                                   (prefix, prefix + "\uffff")).fetchone()[0]
                assert count == 1
        assert repository.read_notice_cover(core.storage, source.document) == source
        assert core.brokers["tasks"]._executor is None


def test_database_error_does_not_authorize_selective_replay(tmp_path, monkeypatch):
    with open_core(tmp_path, notice_work_admission=_budget()) as core:
        _real_origins(core.storage, tmp_path)
        a, b = _rows()
        same_chunk = _admitted("Sans preuve de reprise", "same-chunk")
        next_chunk = _admitted("Lot indépendant", "next-chunk")

        def fail_group(*args, **kwargs):
            raise OSError("simulated live group failure")

        def refuse(*args, **kwargs):
            raise sqlite3.OperationalError("unknown database failure")

        monkeypatch.setattr(repository, "_BATCH_LIMIT", 3)
        monkeypatch.setattr(batch, "_persist_origin_linked_public_cover_group_txn", fail_group)
        monkeypatch.setattr(batch, "request_search_group_repair_txn", refuse)
        monkeypatch.setattr(core.library, "_notice_work_search_committed", _forbid)
        (row, source, own), = _ingest(core, [a, b, same_chunk, next_chunk])
        assert row is next_chunk and own is not None
        assert all(not hasattr(item, "_core_notice_cover_document_ref") for item in (a, b, same_chunk))
        assert _job_refs(core.storage) == ()
        assert repository.read_notice_cover(core.storage, source.document) == source
