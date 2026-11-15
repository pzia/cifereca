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
