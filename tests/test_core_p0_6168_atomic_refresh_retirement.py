"""A producer arriving at the warm refresh retirement cannot lose its epoch."""
from contextlib import closing
from dataclasses import replace
from types import SimpleNamespace as NS

from cifereca.core.notice_cover_repository import is_notice_cover_document
from cifereca.gui.cover_loader import CoverLoader
from cifereca.gui.document_cover_service import DocumentCoverService
from cifereca.gui.notice_cover_cached_reader import cached_notice_cover_snapshot
from cifereca.storage import Storage
from tests.test_core_p0_6366_notice_epoch_refresh_n2 import (
    mirror, NEW_COVER, THIRD_COVER,
)
from tests.test_core_p1_6168_search_remote_document_gateway import anonymous_catalogue


class ManualPool:
    def __init__(self):
        self.pending = []

    def submit(self, work):
        self.pending.append(work)

    def shutdown(self, **kwargs):
        self.pending.clear()


class UnlockProbe:
    """Inject a competing producer immediately after the terminal unlock."""
    def __init__(self, lock):
        self.lock = lock
        self.after_unlock = None

    def __enter__(self):
        self.lock.acquire()
        return self

    def __exit__(self, *_):
        callback, self.after_unlock = self.after_unlock, None
        self.lock.release()
        if callback is not None:
            callback()


def test_producer_at_retirement_owns_a_new_flight_not_erased_by_old_finally(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "images"))
    with closing(Storage(tmp_path / "retirement.sqlite")) as db:
        loader = CoverLoader(max_workers=2)
        reads, network, submitted = [], [], []
        monkeypatch.setattr(loader, "load", lambda url, cb, **kw:
                            network.append(url) or NS(cancel=lambda: None))
        monkeypatch.setattr(loader, "load_preview", lambda url, cb, **kw:
                            network.append(url) or NS(cancel=lambda: None))

        def reader(document):
            snapshot = cached_notice_cover_snapshot(NS(storage=db), document)
            reads.append(snapshot.revision)
            return snapshot

        covers = DocumentCoverService(
            loader, cached_source_reader=reader,
            cached_source_scope=is_notice_cover_document,
        )
        covers._read_pool.shutdown(wait=True)
        pool = covers._read_pool = ManualPool()
        probe = covers._lock = UnlockProbe(covers._lock)
        try:
            first = anonymous_catalogue()
            assert mirror(db, first) == 1
            document = first._core_notice_cover_document_ref
            covers.register(reader(document))
            second = replace(first, thumbnail_url=NEW_COVER)
            assert mirror(db, second) == 1
            assert second._core_notice_cover_document_epoch == 2
            assert covers.refresh_cached_source_if_newer(document, 2)
            assert len(pool.pending) == 1

            def commit_third():
                third = replace(first, thumbnail_url=THIRD_COVER)
                assert mirror(db, third) == 1
                assert third._core_notice_cover_document_epoch == 3
                submitted.append(covers.refresh_cached_source_if_newer(document, 3))

            register = covers._register_snapshot

            def arm_at_registration(snapshot, **kwargs):
                register(snapshot, **kwargs)
                if snapshot.revision == 2:
                    # The next service lock is the worker's stop decision.
                    # A hint injected on its exit must see the retired flight.
                    probe.after_unlock = commit_third

            monkeypatch.setattr(covers, "_register_snapshot", arm_at_registration)
            pool.pending.pop(0)()
            assert submitted == [True] and len(pool.pending) == 1
            assert document in covers._refresh_active
            assert covers._refresh_targets[document] == 3
            pool.pending.pop(0)()
            assert covers._snapshots[document].revision == 3
            assert document not in covers._refresh_active
            assert document not in covers._refresh_targets
            assert reads == [1, 2, 3]
            assert not network
            assert covers.stats()["n2_source_refresh_scheduled"] == 2
        finally:
            covers.close()
            loader.close()
