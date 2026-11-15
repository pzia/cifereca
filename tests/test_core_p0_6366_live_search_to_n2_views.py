"""#6366: a live Core Search supplier reaches exact Notice C -> N2 -> list/detail.

The Core mirror owns the source stamp. GTK never creates Notice C identity or
publishes a second N2 source under a derived ref. No extra provider request.
"""
from __future__ import annotations

from dataclasses import replace
from threading import Event
from types import SimpleNamespace as NS

from cifereca.core import search_orchestration
from cifereca.core.notice_cover_projection import notice_cover_document_ref
from cifereca.core.notice_cover_repository import (
    is_notice_cover_document, read_notice_cover,
)
from cifereca.core.notice_search_cover_producer import admit_public_search_cover
from cifereca.gui.cover_loader import CoverLoader
from cifereca.gui.document_cover_service import DocumentCoverService
from cifereca.gui.notice_cover_cached_reader import cached_notice_cover_snapshot
from cifereca.gui.search_document_cover_bridge import (
    exact_remote_search_document, register_exact_search_document,
    search_document_is_current,
)
from cifereca.storage import Storage
from tests.test_core_p1_6168_search_remote_document_gateway import (
    CATALOG, CONTEXT, anonymous_catalogue, activate_public_search,
    detail, host,
)


def mirror(db, row, identity="alice"):
    return search_orchestration._mirror_remote_results(
        NS(storage=db), [row], identity_id=identity,
    )


def test_real_producer_stamp_is_the_same_document_bound_by_search_list_and_detail(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(search_orchestration.time, "time", lambda: 200)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    db = Storage(tmp_path / "reader.sqlite")
    source = anonymous_catalogue()
    window, _, key = host(source)
    loader = CoverLoader(max_workers=2)
    loaded, disk_reads = [], []
    monkeypatch.setattr(
        loader, "load", lambda uri, cb, **kw: (
            loaded.append(("detail", uri)) or NS(cancel=lambda: None)
        ),
    )
    monkeypatch.setattr(
        loader, "load_preview", lambda uri, cb, **kw: (
            loaded.append(("list", uri)) or NS(cancel=lambda: None)
        ),
    )

    def reader(ref):
        disk_reads.append(ref)
        return cached_notice_cover_snapshot(NS(storage=db), ref)

    service = DocumentCoverService(
        loader, cached_source_reader=reader,
        cached_source_scope=is_notice_cover_document,
    )
    try:
        # Before the real Core supplier pass, the old public N2 scope is a
        # supported transitional path, not a Notice C documentary proof.
        old = exact_remote_search_document(window, source, key)
        assert old is not None
        assert old.document.source_key.startswith("search-exact:")
        assert mirror(db, source) == 1
        document = source._core_notice_cover_document_ref
        stored = read_notice_cover(db, document)
        assert stored is not None and stored.attribute.candidates[0].url == CATALOG
        assert is_notice_cover_document(document)
        assert document.identity_id is None
        actual = exact_remote_search_document(window, source, key)
        assert actual.document == document
        assert actual.document != old.document
        assert search_document_is_current(window, old)
        assert register_exact_search_document(
            window, source, service, actual, document,
        )
        # No Search-owned duplicate N2 snapshot is registered on GTK bind.
        assert service._snapshots == {}
        refreshed = Event()
        initial = service.get_cover(
            document, "list", lambda _: None,
            on_source_changed=refreshed.set,
        )
        assert refreshed.wait(10) and not initial.still_current
        list_view = service.get_cover(document, "list", lambda _: None)
        detail_view = service.get_cover(document, "detail", lambda _: None)
        assert list_view.source_uri == detail_view.source_uri == CATALOG
        assert disk_reads == [document]
        assert ("detail", CATALOG) in loaded

        # B is another viewer, not an image or notice-cache namespace.
        other = anonymous_catalogue()
        assert mirror(db, other, identity="bob") == 1
        assert other._core_notice_cover_document_ref == document
        activate_public_search(window, other, key, "bob", (4, 5, 6, 7))
        second = exact_remote_search_document(window, other, key)
        assert second.document == document
        assert register_exact_search_document(
            window, other, service, second, document,
        )
        assert service.get_cover(document, "detail", lambda _: None).source_uri == CATALOG
        assert disk_reads == [document]
        assert read_notice_cover(db, document).epoch == 1
    finally:
        service.close()
        loader.close()
        db.close()


def test_origin_proof_is_not_downgraded_to_public_search_notice(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(search_orchestration.time, "time", lambda: 200)
    db = Storage(tmp_path / "reader.sqlite")
    try:
        source = anonymous_catalogue()
        window, _, key = host(source)
        assert mirror(db, source) == 1
        assert window._search_cover_bridge.publish(
            source, detail(source), identity_id="alice", context=CONTEXT,
        )
        found = exact_remote_search_document(window, source, key)
        assert found is not None
        assert found.document != source._core_notice_cover_document_ref
        assert [part.tier for part in found.candidates] == ["origin", "catalog"]
    finally:
        db.close()


def test_forged_or_reassigned_source_document_stamps_never_borrow_another_book(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(search_orchestration.time, "time", lambda: 200)
    db = Storage(tmp_path / "reader.sqlite")
    try:
        source = anonymous_catalogue()
        window, _, key = host(source)
        foreign = replace(source, record_id="different-record")
        observation = admit_public_search_cover(
            foreign, identity_id="alice", observed_at=1,
            public_cover_url=CATALOG,
        )
        assert observation is not None
        forged = notice_cover_document_ref(observation.key)
        source._core_notice_cover_document_ref = forged
        current = exact_remote_search_document(window, source, key)
        assert current is not None
        assert current.document != forged
        assert current.document.source_key.startswith("search-exact:")

        # No stale association survives the next real mirror invocation.
        assert mirror(db, source) == 1
        actual = source._core_notice_cover_document_ref
        assert actual != forged
        source.thumbnail_url = "https://covers.example.org/cover.jpg?token=secret"
        assert mirror(db, source) == 1
        assert not hasattr(source, "_core_notice_cover_document_ref")
    finally:
        db.close()


def test_notice_c_readthrough_requires_actual_core_reader_not_fake_n2_register(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(search_orchestration.time, "time", lambda: 200)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    db = Storage(tmp_path / "reader.sqlite")
    loader = CoverLoader(max_workers=2)
    service = DocumentCoverService(loader)
    try:
        source = anonymous_catalogue()
        window, _, key = host(source)
        assert mirror(db, source) == 1
        proof = exact_remote_search_document(window, source, key)
        assert proof is not None and is_notice_cover_document(proof.document)
        assert not register_exact_search_document(
            window, source, service, proof, proof.document,
        )
        assert not service._snapshots
    finally:
        service.close()
        loader.close()
        db.close()
