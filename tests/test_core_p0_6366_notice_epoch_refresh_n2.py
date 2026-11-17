"""#6366/#6168: producer epoch updates an already warm Notice C N2 source.

N2 must be derived from persisted C even on subsequent supplier observations:
one targeted SQLite worker read, a canceled stale visual token and a fresh
list/detail source, without re-fetching suppliers or invalidating physical N3.
"""
from __future__ import annotations

from dataclasses import replace
from threading import Event
from types import SimpleNamespace as NS

from cifereca.core import search_orchestration
from cifereca.core.notice_cover_repository import is_notice_cover_document, read_notice_cover
from cifereca.gui.cover_loader import CoverLoader
from cifereca.gui.document_cover_service import DocumentCoverService
from cifereca.gui.notice_cover_cached_reader import cached_notice_cover_snapshot
from cifereca.gui.search_document_cover_bridge import (
    exact_remote_search_document, register_exact_search_document,
)
from cifereca.storage import Storage
from tests.test_core_p1_6168_search_remote_document_gateway import (
    CATALOG, anonymous_catalogue, host, activate_public_search,
)


NEW_COVER = "https://catalog.example.org/edition-cover-v2.jpg"
THIRD_COVER = "https://catalog.example.org/edition-cover-v3.jpg"


def mirror(db, row, identity="alice"):
    return search_orchestration._mirror_remote_results(
        NS(storage=db), [row], identity_id=identity,
    )


def admitted(window, row, key, covers):
    proof = exact_remote_search_document(window, row, key)
    assert proof is not None
    assert register_exact_search_document(
        window, row, covers, proof, proof.document,
    )
    return proof.document


def test_live_producer_update_refreshes_warm_n2_only_when_epoch_advances(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "images"))
    clock = [200]
    monkeypatch.setattr(search_orchestration.time, "time", lambda: clock[0])
    db = Storage(tmp_path / "notices.sqlite")
    loader = CoverLoader(max_workers=2)
    remote = []
    monkeypatch.setattr(
        loader, "load", lambda url, callback, **kw: (
            remote.append(url) or NS(cancel=lambda: None)
        ),
    )
    monkeypatch.setattr(
        loader, "load_preview", lambda url, callback, **kw: (
            remote.append(url) or NS(cancel=lambda: None)
        ),
    )
    reads = []

    def reader(document):
        reads.append(document)
        return cached_notice_cover_snapshot(NS(storage=db), document)

    covers = DocumentCoverService(
        loader, cached_source_reader=reader,
        cached_source_scope=is_notice_cover_document,
    )
    try:
        initial = anonymous_catalogue()
        window, _, key = host(initial)
        assert mirror(db, initial) == 1
        document = initial._core_notice_cover_document_ref
        assert initial._core_notice_cover_document_epoch == 1
        assert admitted(window, initial, key, covers) == document
        first = Event()
        pending = covers.get_cover(
            document, "detail", lambda _: None,
            on_source_changed=first.set,
        )
        assert pending.source_uri is None and first.wait(10)
        assert covers.get_cover(document, "list", lambda _: None).source_uri == CATALOG
        assert covers.get_cover(document, "detail", lambda _: None).source_uri == CATALOG
        assert reads == [document]
        assert covers.stats()["n2_source_refresh_scheduled"] == 0

        # An identical producer observation does not churn N2 or N3.
        clock[0] = 201
        assert mirror(db, initial) == 1
        assert initial._core_notice_cover_document_epoch == 1
        assert admitted(window, initial, key, covers) == document
        assert reads == [document]

        refreshed = Event()
        old_view = covers.get_cover(
            document, "detail", lambda _: None,
            on_source_changed=refreshed.set,
        )
        assert old_view.source_uri == CATALOG
        updated = replace(initial, thumbnail_url=NEW_COVER)
        clock[0] = 202
        assert mirror(db, updated) == 1
        assert updated._core_notice_cover_document_ref == document
        assert updated._core_notice_cover_document_epoch == 2
        window.search_objects[key] = updated
        assert admitted(window, updated, key, covers) == document
        assert refreshed.wait(10)
        assert not old_view.still_current
        listed = covers.get_cover(document, "list", lambda _: None)
        detailed = covers.get_cover(document, "detail", lambda _: None)
        assert listed.source_uri == detailed.source_uri == NEW_COVER
        assert read_notice_cover(db, document).epoch == 2
        assert reads == [document, document]
        assert covers.stats()["n2_register_changed"] == 1
        assert covers.stats()["n2_source_refresh_scheduled"] == 1

        for _ in range(50):
            assert admitted(window, updated, key, covers) == document
        assert reads == [document, document]  # N2 HIT = no SQLite per rebind.

        # A different viewer reads the same public C source and warm N2,
        # without a profile-specific cache namespace or supplier call.
        other = replace(updated)
        clock[0] = 203
        assert mirror(db, other, identity="bob") == 1
        assert other._core_notice_cover_document_epoch == 2
        activate_public_search(window, other, key, "bob", (4, 5, 6, 7))
        assert admitted(window, other, key, covers) == document
        assert covers.get_cover(document, "detail", lambda _: None).source_uri == NEW_COVER
        assert reads == [document, document]
        assert all(url in (CATALOG, NEW_COVER) for url in remote)
    finally:
        covers.close()
        loader.close()
        db.close()


def test_overlap_of_two_core_epochs_never_loses_latest_revision(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "images"))
    clock = [200]
    monkeypatch.setattr(search_orchestration.time, "time", lambda: clock[0])
    db = Storage(tmp_path / "notices.sqlite")
    loader = CoverLoader(max_workers=2)
    # The asynchronous test is about N2, not the external image transport.
    # Never issue a real HTTP GET from its worker callback.
    monkeypatch.setattr(
        loader, "load",
        lambda url, callback, **kw: NS(cancel=lambda: None),
    )
    monkeypatch.setattr(
        loader, "load_preview",
        lambda url, callback, **kw: NS(cancel=lambda: None),
    )
    entered, release, v3_painted = Event(), Event(), Event()
    reads = []

    def reader(document):
        latest = cached_notice_cover_snapshot(NS(storage=db), document)
        reads.append(latest.revision if latest is not None else None)
        if len(reads) == 2:
            # The worker has already captured v2 while v3 is persisted.
            entered.set()
            assert release.wait(10)
        return latest

    covers = DocumentCoverService(
        loader, cached_source_reader=reader,
        cached_source_scope=is_notice_cover_document,
    )
    try:
        source = anonymous_catalogue()
        window, _, key = host(source)
        assert mirror(db, source) == 1
        document = admitted(window, source, key, covers)
        populated = Event()
        covers.get_cover(
            document, "detail", lambda _: None,
            on_source_changed=populated.set,
        )
        assert populated.wait(10)
        assert covers.get_cover(document, "detail", lambda _: None).source_uri == CATALOG

        def notify():
            if covers.get_cover(
                document, "detail", lambda _: None,
            ).source_uri == THIRD_COVER:
                v3_painted.set()

        subscription = covers.get_cover(
            document, "detail", lambda _: None,
            on_source_changed=notify,
        )
        assert subscription.source_uri == CATALOG
        second = replace(source, thumbnail_url=NEW_COVER)
        clock[0] = 201
        assert mirror(db, second) == 1
        window.search_objects[key] = second
        assert admitted(window, second, key, covers) == document
        assert entered.wait(10)  # v2 worker is blocked.

        third = replace(source, thumbnail_url=THIRD_COVER)
        clock[0] = 202
        assert mirror(db, third) == 1
        assert third._core_notice_cover_document_epoch == 3
        window.search_objects[key] = third
        assert admitted(window, third, key, covers) == document
        release.set()
        assert v3_painted.wait(10)
        assert covers.get_cover(document, "detail", lambda _: None).source_uri == THIRD_COVER
        assert read_notice_cover(db, document).epoch == 3
        assert reads == [1, 2, 3]
        assert covers.stats()["n2_source_refresh_scheduled"] == 1
        # A maliciously large hint is ignored, not retried forever.
        assert covers.refresh_cached_source_if_newer(document, 2**63) is False
    finally:
        release.set()
        covers.close()
        loader.close()
        db.close()


def test_cached_rows_have_no_forged_revision_hint(tmp_path, monkeypatch):
    from cifereca.core.search_orchestration import _stamp_cached_public_notice_refs
    row = anonymous_catalogue()
    row._core_notice_cover_document_epoch = 999
    row._core_notice_cover_document_ref = object()
    _stamp_cached_public_notice_refs([row], identity_id="alice")
    assert not hasattr(row, "_core_notice_cover_document_epoch")
    assert is_notice_cover_document(row._core_notice_cover_document_ref)
    # The cache only reproduces coordinates, never authority over the epoch.


def test_newer_core_epoch_is_not_lost_behind_in_flight_first_n2_miss(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "images"))
    clock = [200]
    monkeypatch.setattr(search_orchestration.time, "time", lambda: clock[0])
    db = Storage(tmp_path / "notices.sqlite")
    loader = CoverLoader(max_workers=2)
    monkeypatch.setattr(
        loader, "load", lambda url, cb, **kw: NS(cancel=lambda: None),
    )
    reads = []
    entered, release, fresh = Event(), Event(), Event()

    def reader(document):
        snapshot = cached_notice_cover_snapshot(NS(storage=db), document)
        reads.append(snapshot.revision if snapshot else None)
        if len(reads) == 1:
            entered.set()
            assert release.wait(10)
        return snapshot

    covers = DocumentCoverService(
        loader, cached_source_reader=reader,
        cached_source_scope=is_notice_cover_document,
    )
    try:
        source = anonymous_catalogue()
        window, _, key = host(source)
        assert mirror(db, source) == 1
        document = admitted(window, source, key, covers)

        def notified():
            read = covers.get_cover(
                document, "detail", lambda _: None,
            )
            if read.source_uri == NEW_COVER:
                fresh.set()

        covers.get_cover(
            document, "detail", lambda _: None,
            on_source_changed=notified,
        )
        assert entered.wait(10)

        updated = replace(source, thumbnail_url=NEW_COVER)
        clock[0] = 201
        assert mirror(db, updated) == 1
        window.search_objects[key] = updated
        assert updated._core_notice_cover_document_epoch == 2
        assert admitted(window, updated, key, covers) == document
        release.set()

        assert fresh.wait(10)
        assert covers.get_cover(document, "detail", lambda _: None).source_uri == NEW_COVER
        assert reads == [1, 2]
        assert covers.stats()["n2_source_refresh_scheduled"] == 1
    finally:
        release.set()
        covers.close()
        loader.close()
        db.close()


def test_positive_producer_epoch_retries_a_preexisting_unknown_n2_miss_once(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "images"))
    monkeypatch.setattr(search_orchestration.time, "time", lambda: 200)
    db = Storage(tmp_path / "notices.sqlite")
    loader = CoverLoader(max_workers=2)
    monkeypatch.setattr(
        loader, "load", lambda url, cb, **kw: NS(cancel=lambda: None),
    )
    source = anonymous_catalogue()
    window, _, key = host(source)
    # The producer C coordinate is already deterministic before its first
    # persistence, but a cache HOT never infers that C source exists.
    from cifereca.core.notice_search_cover_producer import admit_public_search_cover
    from cifereca.core.notice_cover_projection import notice_cover_document_ref
    observation = admit_public_search_cover(
        source, identity_id="alice", observed_at=1,
        public_cover_url=CATALOG,
    )
    assert observation is not None
    doc = notice_cover_document_ref(observation.key)
    entered, release, fresh = Event(), Event(), Event()
    reads = []

    def reader(document):
        observed = cached_notice_cover_snapshot(NS(storage=db), document)
        reads.append(observed.revision if observed else None)
        if len(reads) == 1:
            entered.set()
            assert release.wait(10)
        return observed

    covers = DocumentCoverService(
        loader, cached_source_reader=reader,
        cached_source_scope=is_notice_cover_document,
    )
    try:
        def notified():
            if covers.get_cover(doc, "detail", lambda _: None).source_uri == CATALOG:
                fresh.set()

        covers.get_cover(
            doc, "detail", lambda _: None,
            on_source_changed=notified,
        )
        assert entered.wait(10)
        assert mirror(db, source) == 1
        assert source._core_notice_cover_document_ref == doc
        assert source._core_notice_cover_document_epoch == 1
        assert admitted(window, source, key, covers) == doc
        release.set()
        assert fresh.wait(10)
        assert reads == [None, 1]
        assert covers.get_cover(doc, "detail", lambda _: None).source_uri == CATALOG
    finally:
        release.set()
        covers.close()
        loader.close()
        db.close()

def test_live_search_receipt_pins_the_exact_persisted_epoch(tmp_path):
    from cifereca.gui.search_document_cover_bridge import search_document_is_current
    from tests.test_core_p0_6366_search_c_first_n3_scope import _cell_method

    db = Storage(tmp_path / "pinned-live-epoch.sqlite")
    try:
        item = anonymous_catalogue()
        window, _, key = host(item)
        assert mirror(db, item) == 1
        proof = exact_remote_search_document(window, item, key)
        assert proof is not None
        assert proof.minimum_revision == item._core_notice_cover_document_epoch
        assert search_document_is_current(window, proof)
        row = NS(_cover_search_evidence=proof)
        cell = NS(window=window)
        options = _cell_method("_search_cover_guard")(cell, row)
        assert options["minimum_revision"] == proof.minimum_revision
        assert options["is_current"]()

        # Same SearchResult and DocumentRef, but a changed producer stamp
        # cannot renew the old list/detail callback by mutating its DTO.
        item._core_notice_cover_document_epoch += 1
        assert not options["is_current"]()
        updated = exact_remote_search_document(window, item, key)
        assert updated.document == proof.document
        assert updated.minimum_revision == proof.minimum_revision + 1
    finally:
        db.close()
