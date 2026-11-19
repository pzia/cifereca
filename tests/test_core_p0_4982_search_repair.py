"""#4982 actual Search ingestion -> failed C publication -> durable Core repair."""
from threading import Event

from cifereca.core import notice_c_job_journal as journal
from cifereca.core import notice_c_cover_index as index
from cifereca.core import search_orchestration as search
from cifereca.task_admission import SharedTaskAdmission
from tests.test_core_p0_4982_notice_work_runtime import open_core
from tests.test_core_p0_6366_public_c_sqlite_source_search import verified_public


def _admitted(title, record="repair", **kwargs):
    # Model Core's exact anonymous same-call response witness.
    row = verified_public(record_id=record, title=title, **kwargs)
    row._core_same_call_public_cover_witness = (
        search._CORE_PUBLIC_COVER_WITNESS, search._public_cover_witness_coordinates(row),
    )
    assert search._verified_public_cover_witness(row)
    return row


def test_existing_c_repaired_after_failed_live_search_publication(tmp_path, monkeypatch):
    budget = SharedTaskAdmission(max_inflight=4, reserve_user_slots=1)
    with open_core(tmp_path, notice_work_admission=budget) as core:
        def ingest(title, now, record="repair"):
            pairs = search._ingest_public_search_c(
                core.library, [_admitted(title, record)], identity_id="alice",
                now=now, require_public_cover_witness=True,
            )
            assert len(pairs) == 1
            return pairs[0]

        _, initial_source, old = ingest("Ancien titre", 100)
        _, _, unrelated = ingest("Témoin", 100, "unrelated")
        assert old is not None and unrelated is not None
        assert core.notice_work._broker._executor is None
        committed = Event()
        events = []
        def observe(document, epoch):
            events.append((document, epoch))
            committed.set()
        assert core.notice_work.bind_committed_observer(observe)
        original = index.persist_exact_public_search_cover
        def fail_once(storage, source):
            if source.document == initial_source.document:
                raise OSError("simulated failed live commit")
            return original(storage, source)
        with monkeypatch.context() as patch:
            patch.setattr(index, "persist_exact_public_search_cover", fail_once)
            row, changed, canonical = ingest("Nouveau titre", 101)
        assert canonical is None
        assert changed.epoch > initial_source.epoch
        assert not hasattr(row, "_core_notice_canonical_cover_document_ref")
        assert committed.wait(10)
        latest = index.read_consolidated_notice_cover(core.storage, old.document)
        assert latest.epoch == old.epoch + 1
        assert latest.sources[0].public_bibliography.title == "Nouveau titre"
        assert events == [(old.document, latest.epoch)]
        assert index.read_consolidated_notice_cover(core.storage, unrelated.document) == unrelated
        with core.storage._lock, core.storage._connect() as sql:
            keys = sql.execute("SELECT key FROM metadata WHERE key>=? AND key<?",
                               (journal._PREFIX, journal._END)).fetchall()
            assert len(keys) == 1
            job = journal._load(sql, keys[0][0][len(journal._PREFIX):])
        assert job.state == "current" and job.attempts == 1
        _, _, same = ingest("Nouveau titre", 102)
        assert same.epoch == latest.epoch
        assert core.storage.entitlements() == [] and core.storage.acquisitions() == []


def test_default_core_does_not_install_automatic_search_recovery(tmp_path):
    with open_core(tmp_path) as core:
        assert not hasattr(core.library, "_notice_work_recover_search")



def test_existing_group_repaired_when_only_live_group_publication_fails(tmp_path, monkeypatch):
    from cifereca.core import notice_c_origin_link_producer as origin
    from tests.test_core_p0_6366_library_recent_public_c import _real_origins, A_URL, B_URL

    with open_core(tmp_path, notice_work_admission=SharedTaskAdmission(
            max_inflight=4, reserve_user_slots=1)) as core:
        _real_origins(core.storage, tmp_path)
        def ingest(title, now):
            rows = [
                _admitted(title, "edition-a", source_provider="seller-a", thumbnail_url=A_URL),
                _admitted("Autre source", "edition-b", source_provider="seller-b", thumbnail_url=B_URL),
            ]
            pairs = search._ingest_public_search_c(
                core.library, rows, identity_id="alice", now=now,
                require_public_cover_witness=True,
            )
            assert len(pairs) == 2 and all(own is not None for _, _, own in pairs)
            return pairs
        initial = ingest("Ancien titre", 100)
        group_document = initial[0][0]._core_notice_canonical_cover_document_ref
        old = index.read_consolidated_notice_cover(core.storage, group_document)
        assert len(old.sources) == 2
        committed = Event()
        events = []
        def observe(document, epoch):
            events.append((document, epoch))
            committed.set()
        core.notice_work.bind_committed_observer(observe)
        def fail_group(*args, **kwargs):
            raise OSError("simulated group-only commit failure")
        with monkeypatch.context() as patch:
            patch.setattr(origin, "persist_origin_linked_public_cover_group", fail_group)
            changed = ingest("Nouveau titre", 101)
        # The independent exact self publication has already succeeded.
        assert changed[0][2].document != group_document
        assert changed[0][2].sources[0].public_bibliography.title == "Nouveau titre"
        assert committed.wait(10)
        latest = index.read_consolidated_notice_cover(core.storage, group_document)
        assert latest.epoch == old.epoch + 1
        assert {s.public_bibliography.title for s in latest.sources} == {
            "Nouveau titre", "Autre source",
        }
        assert events == [(group_document, latest.epoch)]
        assert core.storage.entitlements() == [] and core.storage.acquisitions() == []
