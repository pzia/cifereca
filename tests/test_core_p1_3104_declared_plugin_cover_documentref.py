"""#3104/#6168: neutral plugin list and detail use the same attested N2 ref."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.core.book_cover_projection import CoverDocumentScope
from cifereca.core.covers import candidate
from cifereca.gui.cover_loader import CoverLoader
from cifereca.gui.document_cover_service import (
    CoverSourceSnapshot, DocumentCoverService,
)
from cifereca.gui.declared_document_cover import bind_declared_document_cover
from cifereca.plugin_api.models import (
    UiBookListEntry, UiBookDetailPresentation,
)


ROOT = Path(__file__).resolve().parents[1] / "src/cifereca/gui"
PUBLIC = CoverDocumentScope(
    kind="public_catalogue", identity_id=None, source_key="notice-c:v1:" + "a" * 64,
    provider_id="bnf", credential_id="__public__",
)
OTHER = CoverDocumentScope(
    kind="public_catalogue", identity_id=None, source_key="notice-c:v1:" + "b" * 64,
    provider_id="bnf", credential_id="__public__",
)
URL = "https://covers.example.org/admitted.jpg"
FOREIGN_URL = "https://covers.example.org/untrusted-plugin-url.jpg"


class FakeCell:
    def __init__(self, *args):
        (self.key, self.title, self.subtitle, self.meta, self.badge,
         self.thumbnail_url) = args


class FakeCover:
    def __init__(self):
        self.calls = []
    def bind_document(self, *args, **kw):
        self.calls.append(("document", args, kw))
    def bind_url(self, *args, **kw):
        self.calls.append(("legacy", args, kw))


def show_rows_function():
    source = (ROOT / "public_book_view.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    obj = next(node for node in tree.body if isinstance(node, ast.ClassDef)
               and node.name == "PublicBookMasterDetailView")
    method = next(node for node in obj.body if isinstance(node, ast.FunctionDef)
                  and node.name == "show_rows")
    ns = {"BookListItem": FakeCell, "CoverDocumentScope": CoverDocumentScope}
    module = ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[]))
    exec(compile(module, str(ROOT / "public_book_view.py"), "exec"), ns)
    return ns["show_rows"]


def test_public_plugin_rows_pass_exact_ref_through_shared_booklistcell():
    show_rows = show_rows_function()
    rows = []
    host = NS(
        _rows={},
        _page=NS(show_rows=lambda data, total_count=None: rows.extend(data)),
    )
    public = UiBookListEntry(
        "book-a", "Titre", thumbnail_url=FOREIGN_URL,
        cover_document_ref=PUBLIC,
    )
    legacy = UiBookListEntry("book-b", "Autre", thumbnail_url=FOREIGN_URL)
    show_rows(host, [public, legacy])
    assert rows[0]._cover_document_ref == PUBLIC
    assert rows[0].thumbnail_url == FOREIGN_URL
    assert not hasattr(rows[1], "_cover_document_ref")
    assert rows[1].thumbnail_url == FOREIGN_URL
    assert host._rows == {"book-a": public, "book-b": legacy}


def test_forged_object_ref_does_not_enable_legacy_url_fallback():
    show_rows = show_rows_function()
    captured = []
    host = NS(
        _rows={},
        _page=NS(show_rows=lambda rows, total_count=None: captured.extend(rows)),
    )
    bad = UiBookListEntry(
        "bad", "Bad", thumbnail_url=FOREIGN_URL,
        cover_document_ref=object(),
    )
    show_rows(host, [bad])
    assert captured[0].thumbnail_url is None
    assert not hasattr(captured[0], "_cover_document_ref")


def test_list_ref_changes_without_url_change_and_keeps_original_row_key():
    show_rows = show_rows_function()
    outputs = []
    host = NS(_rows={}, _page=NS(show_rows=lambda rows, total_count=None:
                                 outputs.append(list(rows))))
    first = UiBookListEntry(
        "same-key", "Edition A", thumbnail_url=FOREIGN_URL,
        cover_document_ref=PUBLIC,
    )
    second = UiBookListEntry(
        "same-key", "Edition B", thumbnail_url=FOREIGN_URL,
        cover_document_ref=OTHER,
    )
    show_rows(host, [first])
    show_rows(host, [second])
    assert outputs[0][0]._cover_document_ref == PUBLIC
    assert outputs[1][0]._cover_document_ref == OTHER
    assert outputs[0][0].key == outputs[1][0].key == "same-key"
    assert host._rows["same-key"] is second


def test_exact_document_ref_binds_n2_and_never_requests_untrusted_url(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    loader = CoverLoader(max_workers=2)
    calls = []
    monkeypatch.setattr(
        loader, "load", lambda uri, cb, **kw: (
            calls.append(uri) or NS(cancel=lambda: None)
        ),
    )
    service = DocumentCoverService(loader)
    try:
        service.register(CoverSourceSnapshot(
            scope=PUBLIC, revision=7,
            candidates=(candidate(
                URL, tier="catalog", source="notice-c", evidence_id=PUBLIC.source_key,
            ),),
        ))
        cover = FakeCover()
        assert bind_declared_document_cover(
            cover, loader, service, PUBLIC, variant="detail", priority=0,
        ) is True
        assert cover.calls == [
            ("document", (service, PUBLIC), {"variant": "detail", "priority": 0}),
        ]
        request = service.get_cover(PUBLIC, "detail", lambda _: None)
        assert request.source_uri == URL
        assert calls == [URL]
        assert FOREIGN_URL not in calls
    finally:
        service.close()
        loader.close()


def test_invalid_detail_ref_displays_placeholder_no_legacy_source(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    loader = CoverLoader(max_workers=2)
    service = DocumentCoverService(loader)
    try:
        for bad in (None, object(), "https://covers.example.org/forged.png"):
            cover = FakeCover()
            assert not bind_declared_document_cover(
                cover, loader, service, bad, variant="detail", priority=0,
            )
            assert cover.calls == [
                ("legacy", (loader, None), {"priority": 0}),
            ]
    finally:
        service.close()
        loader.close()


def test_detail_panel_consumes_and_clears_exact_document_ref_during_render():
    tree = ast.parse((ROOT / "detail_panel.py").read_text(encoding="utf-8"))
    cls = next(x for x in tree.body if isinstance(x, ast.ClassDef)
               and x.name == "DetailPanel")
    def source(name):
        fn = next(x for x in cls.body if isinstance(x, ast.FunctionDef)
                  and x.name == name)
        return ast.unparse(fn)
    render = source("show_presentation")
    header = source("_cover_and_title")
    assert "presentation, 'cover_document_ref'" in render.replace('"', "'")
    assert "'_next_cover_document_ref'" in render.replace('"', "'")
    assert "finally:" in render
    assert "bind_declared_document_cover" in header
    assert "self._bind_cover(cover, thumbnail, cover_evidence=evidence)" in header
    assert header.index("if evidence is not None:") < header.index("elif document is not None:")



def test_plugin_list_and_detail_request_same_exact_n2_source_not_a_plugin_url(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    loader = CoverLoader(max_workers=2)
    observed = []
    monkeypatch.setattr(
        loader, "load_preview", lambda uri, cb, **kw: (
            observed.append(("list", uri)) or NS(cancel=lambda: None)
        ),
    )
    monkeypatch.setattr(
        loader, "load", lambda uri, cb, **kw: (
            observed.append(("detail", uri)) or NS(cancel=lambda: None)
        ),
    )
    service = DocumentCoverService(loader)
    try:
        service.register(CoverSourceSnapshot(
            scope=PUBLIC, revision=1,
            candidates=(candidate(
                URL, tier="catalog", source="notice-c",
                evidence_id=PUBLIC.source_key,
            ),),
        ))
        row = UiBookListEntry(
            "public:a", "Livre", thumbnail_url=FOREIGN_URL,
            cover_document_ref=PUBLIC,
        )
        details = UiBookDetailPresentation(
            "Livre", thumbnail_url=FOREIGN_URL,
            cover_document_ref=PUBLIC,
        )
        show_rows = show_rows_function()
        generated = []
        host = NS(
            _rows={}, _page=NS(show_rows=lambda rows, total_count=None:
                                generated.extend(rows)),
        )
        show_rows(host, [row])
        assert generated[0]._cover_document_ref == details.cover_document_ref
        listed = service.get_cover(generated[0]._cover_document_ref, "list", lambda _: None)
        described = service.get_cover(details.cover_document_ref, "detail", lambda _: None)
        assert listed.source_uri == described.source_uri == URL
        assert service.decisions.peek(PUBLIC, revision=1).uri == URL
        assert observed == [("list", URL), ("detail", URL)]
        assert FOREIGN_URL not in repr(observed)
    finally:
        service.close()
        loader.close()


def test_core_admitted_ref_outranks_an_optional_plugin_reference():
    show_rows = show_rows_function()
    output = []
    host = NS(_rows={}, _page=NS(show_rows=lambda rows, total_count=None:
                                 output.extend(rows)))
    row = NS(
        key="exact", title="Livre", subtitle="", meta="", badge="",
        thumbnail_url=FOREIGN_URL,
        _cover_document_ref=PUBLIC, cover_document_ref=OTHER,
    )
    show_rows(host, [row])
    assert output[0]._cover_document_ref == PUBLIC
    assert host._rows["exact"] is row
