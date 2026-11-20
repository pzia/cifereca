"""A recycled initiating view must not strand the other views of the same C."""
from threading import Event
from types import SimpleNamespace as NS

from cifereca.core.notice_cover_projection import notice_cover_document_ref
from cifereca.core.notice_cover_repository import is_notice_cover_document
from cifereca.core.notice_search_cover_producer import admit_public_search_cover
from cifereca.gui.cover_loader import CoverLoader
from cifereca.gui.document_cover_service import DocumentCoverService
from cifereca.gui.notice_cover_cached_reader import cached_notice_cover_snapshot
from cifereca.storage import Storage
from tests.test_core_p0_6366_notice_epoch_refresh_n2 import mirror
from tests.test_core_p1_6168_search_remote_document_gateway import (
    CATALOG, anonymous_catalogue,
)


def test_cancelled_first_miss_keeps_other_views_and_one_exact_retry(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "images"))
    db = Storage(tmp_path / "notices.sqlite")
    loader = CoverLoader(max_workers=2)
    network = []
    monkeypatch.setattr(loader, "load", lambda url, cb, **kw:
                        network.append(url) or NS(cancel=lambda: None))
    monkeypatch.setattr(loader, "load_preview", lambda url, cb, **kw:
                        network.append(url) or NS(cancel=lambda: None))
    row = anonymous_catalogue()
    observation = admit_public_search_cover(
        row, identity_id="alice", observed_at=1, public_cover_url=CATALOG,
    )
    assert observation is not None
    document = notice_cover_document_ref(observation.key)
    entered, release, second_notified, limited_notified = (
        Event(), Event(), Event(), Event(),
    )
    reads = []
    cancelled_notifications = []

    def reader(ref):
        snapshot = cached_notice_cover_snapshot(NS(storage=db), ref)
        reads.append(snapshot.revision if snapshot else None)
        if len(reads) == 1:
            entered.set()
            assert release.wait(10)
        return snapshot

    covers = DocumentCoverService(
        loader, cached_source_reader=reader,
        cached_source_scope=is_notice_cover_document, max_documents=1,
    )
    handles = []
    try:
        first = covers.get_cover(
            document, "list", lambda _: None,
            on_source_changed=lambda: cancelled_notifications.append(True),
        )
        assert entered.wait(10)
        second = covers.get_cover(
            document, "detail", lambda _: None,
            on_source_changed=second_notified.set,
        )
        # Fill the public subscription budget (8 for this service), then
        # exercise a live consumer that relies on the bounded waiter list.
        for _ in range(6):
            handles.append(covers.get_cover(
                document, "list", lambda _: None, on_source_changed=lambda: None,
            ))
        limited = covers.get_cover(
            document, "list", lambda _: None, on_source_changed=limited_notified.set,
        )
        assert limited._cancel_source_subscription is None
        first.cancel()
        for handle in handles:
            handle.cancel()
        assert mirror(db, row) == 1
        assert row._core_notice_cover_document_ref == document
        covers.refresh_cached_source_if_newer(document, row._core_notice_cover_document_epoch)
        release.set()
        assert second_notified.wait(10)
        assert limited_notified.wait(10)
        assert reads == [None, 1]
        assert not cancelled_notifications
        assert network == []
        assert covers.get_cover(document, "detail", lambda _: None).source_uri == CATALOG
        assert reads == [None, 1]
        second.cancel()
        limited.cancel()
    finally:
        release.set()
        covers.close()
        loader.close()
        db.close()
