"""#4982: committed canonical revision -> existing shared N2 service."""
from threading import Event
from types import SimpleNamespace as NS

from cifereca.core import notice_c_job_journal as journal
from cifereca.core import notice_c_exact_recompute as exact
from cifereca.core.notice_c_cover_index import (
    persist_exact_public_search_cover, read_consolidated_notice_cover,
)
from cifereca.core.notice_c_work_pump import PublicNoticeWorkPump
from cifereca.core.notice_c_work_bridge import AggregateWorkBridge, PreparedAggregate
from cifereca.gui.cover_loader import CoverLoader
from cifereca.gui.document_cover_service import DocumentCoverService
from cifereca.gui.notice_cover_cached_reader import cached_notice_cover_snapshot
from cifereca.storage import Storage
from cifereca.task_admission import SharedTaskAdmission
from cifereca.task_runtime import TaskBrokerImpl
from tests.test_core_p0_4982_exact_public_recompute import source
from tests.test_core_p0_6366_public_c_sqlite_source_search import persist, verified_public

URL = "https://covers.example.org/old.jpg"
NEW = "https://covers.example.org/new.jpg"


def row(record, url):
    return verified_public(record_id=record, title="Un livre", thumbnail_url=url)


def test_commit_reloads_only_target_and_revokes_old_n2_token(tmp_path, monkeypatch):
    db = Storage(tmp_path / "n2.sqlite")
    loader = CoverLoader(max_workers=1)
    broker = TaskBrokerImpl(object(), max_workers=1, shared_admission=
                            SharedTaskAdmission(max_inflight=3, reserve_user_slots=1))
    reads, remote, events = [], [], []
    def reader(document):
        reads.append(document)
        return cached_notice_cover_snapshot(NS(storage=db), document)
    def load(url, *args, **kwargs):
        remote.append(url)
        return NS(cancel=lambda: None)
    monkeypatch.setattr(loader, "load", load)
    monkeypatch.setattr(loader, "load_preview", load)
    covers = DocumentCoverService(loader, cached_source_reader=reader)
    try:
        first = persist_exact_public_search_cover(db, persist(db, row("same", URL), at=10))
        other = persist_exact_public_search_cover(db, persist(db, row("other", URL), at=10))
        covers.register(reader(first.document))
        covers.register(reader(other.document))
        reads.clear()
        changed = Event()
        old = covers.get_cover(first.document, "list", lambda _: None,
                               on_source_changed=changed.set)
        unaffected = covers.get_cover(other.document, "detail", lambda _: None)
        assert old.source_uri == unaffected.source_uri == URL
        remote.clear()
        persist(db, row("same", NEW), at=11)
        ref = exact.request_exact_public_recompute(
            db, first.document, now=100, expected_generation=0)
        def committed(document, epoch):
            # Read on a new connection: both writes must be visible.
            assert journal.read_job(db, ref).state == "current"
            assert read_consolidated_notice_cover(db, document).epoch == epoch
            events.append((document, epoch))
            assert covers.refresh_committed_source_in_worker(document, epoch)
        pump = PublicNoticeWorkPump(db, broker, clock=lambda: 100,
                                    on_committed=committed)
        result, = pump.tick().handles
        assert result.result(timeout=10) == "current"
        assert events == [(first.document, first.epoch + 1)]
        assert changed.is_set() and not old.still_current
        assert unaffected.still_current
        assert reads == [first.document] and not remote
        assert covers.get_cover(first.document, "detail", lambda _: None).source_uri == NEW
        assert covers.get_cover(other.document, "list", lambda _: None).source_uri == URL
        assert not covers.refresh_committed_source_in_worker(first.document, first.epoch)
        assert reads == [first.document]
        # True no-op publication receives no notification, hence no reads.
        again = exact.request_exact_public_recompute(
            db, first.document, now=100, expected_generation=ref.generation)
        handle, = pump.tick().handles
        assert handle.result(timeout=10) == "current"
        assert journal.read_job(db, again).state == "current"
        assert len(events) == 1 and reads == [first.document]
    finally:
        covers.close()
        loader.close()
        broker.close()
        db.close()


def test_rollback_suppresses_notification_and_observer_failure_is_not_retry(tmp_path):
    db = Storage(tmp_path / "notify.sqlite")
    broker = TaskBrokerImpl(object(), max_workers=1, shared_admission=
                            SharedTaskAdmission(max_inflight=3, reserve_user_slots=1))
    try:
        first = persist_exact_public_search_cover(db, source(db, "Ancien", 10))
        source(db, "Nouveau", 11)
        ref = exact.request_exact_public_recompute(
            db, first.document, now=100, expected_generation=0)
        events = []
        now = [100]
        def notify(*event):
            events.append(event)
            raise RuntimeError("observer failure")
        def prepare(spec, control):
            publish = exact.prepare_exact_public_recompute(db, spec)
            def aborted(sql):
                publish(sql)
                return False
            return PreparedAggregate(aborted)
        bridge = AggregateWorkBridge(db, broker, prepare, clock=lambda: now[0],
                                     on_committed=notify)
        assert bridge.submit(ref).result(timeout=10) == "transient_failure"
        assert events == [] and read_consolidated_notice_cover(db, first.document) == first
        now[0] = 101
        pump = PublicNoticeWorkPump(db, broker, clock=lambda: now[0],
                                    on_committed=notify)
        handle, = pump.tick().handles
        assert handle.result(timeout=10) == "current"
        assert journal.read_job(db, ref).state == "current"
        assert events == [(first.document, first.epoch + 1)]
    finally:
        broker.close()
        db.close()


def test_cold_document_is_not_loaded_by_notification(tmp_path):
    db = Storage(tmp_path / "cold.sqlite")
    loader = CoverLoader(max_workers=1)
    reads = []
    covers = DocumentCoverService(loader, cached_source_reader=lambda doc: reads.append(doc))
    try:
        c = persist_exact_public_search_cover(db, source(db, "Titre", 10))
        assert not covers.refresh_committed_source_in_worker(c.document, c.epoch)
        assert reads == []
    finally:
        covers.close()
        loader.close()
        db.close()
