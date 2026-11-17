"""P0 #6366/#3724: observed Numilog public HTML -> live session -> C -> N2.

No invented SearchResult, supplier GET, provider-owned SQLite writer, or GTK
fixture. The exact public session parser/adapter, Core mirror, persisted
source reader and shared N2 implementation are exercised together.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from threading import Event
from types import SimpleNamespace as NS

from cifereca.core import search_orchestration
from cifereca.core.notice_cover_repository import (
    is_notice_cover_document, read_notice_cover,
)
from cifereca.gui.cover_loader import CoverLoader
from cifereca.gui.document_cover_service import DocumentCoverService
from cifereca.gui.notice_cover_cached_reader import cached_notice_cover_snapshot
from cifereca.gui.search_cover_bridge import SearchCoverBridge
from cifereca.gui.search_document_cover_bridge import (
    SearchDocumentCoverRegistry, exact_remote_search_document,
    register_exact_search_document,
)
from cifereca.storage import Storage
from cifereca_numilog_public import parse_public_search
import cifereca_numilog_public_provider as provider


SOURCE = (
    "https://www.numilog.com/Pages/Recherche/ResultatRecherche.aspx"
    "?mode=brSimple&query=loup"
)
FIXTURE = Path(__file__).parent / "fixtures" / "observed_search-loup-0.html"


def real_search(monkeypatch):
    parsed = parse_public_search(FIXTURE.read_text(encoding="utf-8"), SOURCE)
    assert parsed.items and parsed.items[0].thumbnail_url
    fetches = []
    monkeypatch.setattr(
        provider, "fetch_public_search",
        lambda *args, **kwargs: fetches.append((args, kwargs)) or parsed,
    )
    session = provider.NumilogPublicSession(
        credential_id="__public__", label="Public",
        credentials={}, options={},
    )
    session.connect()
    results = session.search("loup", scope="all", field="all")
    assert results and len(fetches) == 1
    assert all(
        row.source_provider == "numilog-catalog"
        and row.source_credential == "__public__"
        and row.source_collection == "public_catalog"
        and row.owned is False and row.downloadable is False
        and row.download_url is None
        for row in results
    )
    return session, results, fetches


def active_window(row, *, identity_id="alice", context=(1, 2, 3, 4)):
    key = row.source_provider + ":" + row.id
    return NS(
        _closed=False, _search_mode="global",
        _identity_context_generation=context[0],
        _search_generation=context[1],
        _online_cover_context=context,
        _loaded_search_context=("loup", "all", "all", identity_id),
        search_controls=NS(
            state=lambda: NS(text="loup", field="all", scope="all"),
        ),
        service=NS(
            current_identity=NS(identity_id=identity_id),
            _account_context_epoch=context[2],
            _source_context_epoch=context[3],
        ),
        search_objects={key: row},
        _search_cover_bridge=SearchCoverBridge(),
        search_document_cover_registry=SearchDocumentCoverRegistry(),
    ), key


def test_observed_numilog_session_to_persisted_notice_and_same_list_detail_n2(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "covers"))
    monkeypatch.setattr(search_orchestration.time, "time", lambda: 200)
    session, rows, fetches = real_search(monkeypatch)
    db = Storage(tmp_path / "notices.sqlite")
    loader = CoverLoader(max_workers=2)
    reads, paint = [], []
    monkeypatch.setattr(
        loader, "load", lambda url, cb, **kw: (
            paint.append(("detail", url)) or NS(cancel=lambda: None)
        ),
    )
    monkeypatch.setattr(
        loader, "load_preview", lambda url, cb, **kw: (
            paint.append(("list", url)) or NS(cancel=lambda: None)
        ),
    )
    n2 = DocumentCoverService(
        loader,
        cached_source_reader=lambda ref: (
            reads.append(ref)
            or cached_notice_cover_snapshot(NS(storage=db), ref)
        ),
        cached_source_scope=is_notice_cover_document,
    )
    try:
        # No synthetic proof: use the real provider's parsed SearchResult.
        row = rows[0]
        assert search_orchestration._mirror_remote_results(
            NS(storage=db), [row], identity_id="alice",
        ) == 1
        document = row._core_notice_cover_document_ref
        assert is_notice_cover_document(document)
        assert document.identity_id is None
        assert read_notice_cover(db, document).attribute.candidates[0].url == row.thumbnail_url

        window, key = active_window(row)
        packet = exact_remote_search_document(window, row, key)
        assert packet is not None and packet.document == document
        assert register_exact_search_document(window, row, n2, packet, document)
        assert not n2._snapshots  # No duplicate Search-owned source.
        changed = Event()
        initial = n2.get_cover(
            document, "list", lambda _path: None,
            on_source_changed=changed.set,
        )
        assert changed.wait(10) and not initial.still_current
        listed = n2.get_cover(document, "list", lambda _: None)
        detail = n2.get_cover(document, "detail", lambda _: None)
        assert listed.source_uri == detail.source_uri == row.thumbnail_url
        assert reads == [document]
        assert ("detail", row.thumbnail_url) in paint

        # A and B are viewers of ONE anonymous source and ONE N2 cache.
        assert search_orchestration._mirror_remote_results(
            NS(storage=db), [row], identity_id="bob",
        ) == 1
        assert row._core_notice_cover_document_ref == document
        assert read_notice_cover(db, document).epoch == 1
        assert n2.get_cover(document, "detail", lambda _: None).source_uri == row.thumbnail_url
        assert reads == [document]
        assert len(fetches) == 1
        with db._lock, db._connect() as conn:
            assert conn.execute(
                "SELECT count(*) FROM metadata "
                "WHERE key LIKE 'notice.c.observation.v1:%'"
            ).fetchone()[0] == 1
    finally:
        n2.close()
        loader.close()
        db.close()
        session.close()


def test_observed_source_edition_changes_not_joined_by_image_and_no_rights(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(search_orchestration.time, "time", lambda: 200)
    session, rows, fetches = real_search(monkeypatch)
    db = Storage(tmp_path / "notices.sqlite")
    try:
        item = rows[0]
        edition = replace(
            item, id=item.id + ":other-edition",
            record_id=item.record_id + ":other-edition",
        )
        assert search_orchestration._mirror_remote_results(
            NS(storage=db), [item, edition], identity_id="alice",
        ) == 2
        a, b = item._core_notice_cover_document_ref, edition._core_notice_cover_document_ref
        assert a != b
        assert read_notice_cover(db, a).attribute.candidates[0].url == item.thumbnail_url
        assert read_notice_cover(db, b).attribute.candidates[0].url == item.thumbnail_url
        assert len(fetches) == 1
        assert item.owned is False and edition.downloadable is False
        signed = replace(item, thumbnail_url=item.thumbnail_url + "?token=secret")
        assert search_orchestration._mirror_remote_results(
            NS(storage=db), [signed], identity_id="alice",
        ) == 1
        assert not hasattr(signed, "_core_notice_cover_document_ref")
        with db._lock, db._connect() as conn:
            assert conn.execute(
                "SELECT count(*) FROM metadata "
                "WHERE key LIKE 'notice.c.observation.v1:%'"
            ).fetchone()[0] == 2
    finally:
        db.close()
        session.close()


def test_observed_supplier_c_to_real_n2_n3_metrics_and_single_image_get(
    tmp_path, monkeypatch,
):
    """A real parsed book uses one physical N3 cache across list/detail/A/B.

    MockTransport replaces ONLY the image server; N2 and N3 are production
    implementations, not mocked loaders. Store safe counters rather than
    profile names, supplier HTTP bodies or filesystem paths in diagnostics.
    """
    from io import BytesIO

    import httpx
    from PIL import Image

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "covers"))
    monkeypatch.setattr(search_orchestration.time, "time", lambda: 200)
    session, rows, search_fetches = real_search(monkeypatch)
    db = Storage(tmp_path / "notices.sqlite")
    loader = CoverLoader(max_workers=2)
    reads = []
    image_gets = []
    response = BytesIO()
    Image.new("RGB", (18, 24), (20, 90, 180)).save(response, format="PNG")
    picture = response.getvalue()

    def serve(request):
        assert request.method == "GET"
        assert str(request.url) == rows[0].thumbnail_url
        image_gets.append("GET")
        return httpx.Response(
            200, headers={"content-type": "image/png"},
            content=picture, request=request,
        )

    loader.client.close()
    loader.client = httpx.Client(
        transport=httpx.MockTransport(serve), trust_env=False,
    )
    covers = DocumentCoverService(
        loader,
        cached_source_reader=lambda ref: (
            reads.append(ref)
            or cached_notice_cover_snapshot(NS(storage=db), ref)
        ),
        cached_source_scope=is_notice_cover_document,
    )
    try:
        row = rows[0]
        assert search_orchestration._mirror_remote_results(
            NS(storage=db), [row], identity_id="alice",
        ) == 1
        doc = row._core_notice_cover_document_ref
        source_ready = Event()
        first = covers.get_cover(
            doc, "detail", lambda _: None,
            on_source_changed=source_ready.set,
        )
        assert first.source_uri is None
        assert source_ready.wait(10)
        assert reads == [doc]

        done_detail = Event()
        rendered_detail = []
        detail = covers.get_cover(
            doc, "detail",
            lambda file: (rendered_detail.append(file), done_detail.set()),
        )
        assert detail.source_uri == row.thumbnail_url
        assert done_detail.wait(10)
        assert rendered_detail[0] is not None and rendered_detail[0].is_file()
        assert image_gets == ["GET"]
        assert loader.stats()["download_ok"] == 1

        done_list = Event()
        rendered_list = []
        listing = covers.get_cover(
            doc, "list",
            lambda file: (rendered_list.append(file), done_list.set()),
        )
        assert listing.source_uri == detail.source_uri
        assert done_list.wait(10)
        assert rendered_list[0] is not None and rendered_list[0].is_file()
        assert image_gets == ["GET"]  # warm image, no GET on list
        n3_before = loader.stats()
        assert n3_before["cache_hit_valid"] >= 1
        # Plugin-only CI may lack GdkPixbuf, which is required to create a
        # preview. The real CoverLoader must then return the validated source
        # image rather than attempt a second supplier GET. On GTK hosts with
        # the decoder, the generated preview is reused on the next list bind.
        created = n3_before["preview_created"]
        fallback = n3_before["preview_fallback"]
        assert created + fallback >= 1
        if created:
            assert rendered_list[0].parent == loader.preview_root
        else:
            assert rendered_list[0] == rendered_detail[0]

        again = Event()
        cached = covers.get_cover(doc, "list", lambda _: again.set())
        assert cached.source_uri == row.thumbnail_url and again.wait(10)
        assert image_gets == ["GET"]
        if created:
            assert loader.stats()["preview_hit"] >= 1
        else:
            assert loader.stats()["preview_fallback"] >= fallback

        # Bob views the same anonymous source on the same desktop. Neither
        # GET nor N2 admission needs repeating; source/edition stays stable.
        assert search_orchestration._mirror_remote_results(
            NS(storage=db), [row], identity_id="bob",
        ) == 1
        assert row._core_notice_cover_document_ref == doc
        from_b = Event()
        covers.get_cover(doc, "detail", lambda _: from_b.set())
        assert from_b.wait(10)
        assert image_gets == ["GET"] and len(search_fetches) == 1
        assert reads == [doc]
        n2 = covers.stats()
        n3 = loader.stats()
        assert n2["n2_miss"] >= 1
        assert n2["n2_hit"] >= 3
        assert n2["n2_registered_documents"] == 1
        assert n3["download_ok"] == 1
        assert n3["cache_hit_valid"] >= 2
        assert read_notice_cover(db, doc).epoch == 1
    finally:
        covers.close()
        loader.close()
        db.close()
        session.close()


def test_two_distinct_notice_editions_share_n3_bytes_not_n2_ownership(
    tmp_path, monkeypatch,
):
    """Exact C document identities remain separate even with the same PNG."""
    from io import BytesIO
    import httpx
    from PIL import Image

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "covers"))
    monkeypatch.setattr(search_orchestration.time, "time", lambda: 200)
    session, observed, supplier_gets = real_search(monkeypatch)
    db = Storage(tmp_path / "notices.sqlite")
    loader = CoverLoader(max_workers=2)
    image_gets = []
    payload = BytesIO()
    Image.new("RGB", (20, 28), (200, 50, 110)).save(payload, "PNG")

    def image_response(request):
        assert request.method == "GET"
        assert str(request.url) == observed[0].thumbnail_url
        image_gets.append(1)
        return httpx.Response(
            200, headers={"content-type": "image/png"},
            content=payload.getvalue(), request=request,
        )

    loader.client.close()
    loader.client = httpx.Client(
        transport=httpx.MockTransport(image_response), trust_env=False,
    )
    reads = []
    covers = DocumentCoverService(
        loader, cached_source_reader=lambda ref: (
            reads.append(ref)
            or cached_notice_cover_snapshot(NS(storage=db), ref)
        ),
        cached_source_scope=is_notice_cover_document,
    )
    try:
        first = observed[0]
        second = replace(
            first, record_id=first.record_id + ":independent-edition",
            id=first.id + ":independent-edition",
        )
        assert first.thumbnail_url == second.thumbnail_url
        assert search_orchestration._mirror_remote_results(
            NS(storage=db), [first, second], identity_id="alice",
        ) == 2
        ref_a = first._core_notice_cover_document_ref
        ref_b = second._core_notice_cover_document_ref
        assert ref_a != ref_b
        assert ref_a.identity_id is ref_b.identity_id is None
        assert read_notice_cover(db, ref_a).epoch == 1
        assert read_notice_cover(db, ref_b).epoch == 1

        def warm(ref):
            ready = Event()
            covers.get_cover(
                ref, "detail", lambda _: None,
                on_source_changed=ready.set,
            )
            assert ready.wait(10)
            done = Event()
            accepted = []
            chosen = covers.get_cover(
                ref, "detail",
                lambda file: (accepted.append(file), done.set()),
            )
            assert chosen.source_uri == first.thumbnail_url
            assert done.wait(10)
            assert accepted[0] is not None and accepted[0].is_file()
            return chosen

        warm(ref_a)  # N2/C source a, N3 physical MISS.
        assert image_gets == [1]
        warm(ref_b)  # N2/C source b, N3 physical HIT.
        assert image_gets == [1] and reads == [ref_a, ref_b]
        assert covers.stats()["n2_registered_documents"] == 2
        assert loader.stats()["download_ok"] == 1
        assert loader.stats()["cache_hit_valid"] >= 1
        assert len(supplier_gets) == 1

        # Evict only the physical image: both independent documentary N2
        # associations remain valid and one new N3 GET is allowed on demand.
        physical = loader._path(first.thumbnail_url)
        assert physical.is_file()
        physical.unlink()
        reloaded = Event()
        again = covers.get_cover(ref_b, "detail", lambda _: reloaded.set())
        assert again.source_uri == first.thumbnail_url
        assert reloaded.wait(10)
        assert image_gets == [1, 1]
        assert reads == [ref_a, ref_b]  # Never requalify Notice C on N3 MISS.
        assert covers.stats()["n2_registered_documents"] == 2
        assert len(supplier_gets) == 1
    finally:
        covers.close()
        loader.close()
        db.close()
        session.close()
