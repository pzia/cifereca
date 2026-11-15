"""#6168: N2 source selection is still exact at the GTK paint sink.

An asynchronous N3 result can be valid on the worker and invalid by the time
GLib runs the paint callback. Neither the physical file nor the GTK bind key
can replace the N2 CAS token.
"""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.core.book_cover_projection import CoverDocumentScope
from cifereca.core.covers import candidate
from cifereca.gui.cover_loader import CoverLoader
from cifereca.gui.document_cover_service import (
    CoverReadRequest, CoverSourceSnapshot, DocumentCoverService,
)
from tests.test_core_p1_6168_gtk_texture_applied_hook import env

ROOT = Path(__file__).resolve().parents[1] / "src/cifereca/gui"
URI = "https://public.example.org/exact-source.png"


def _scope():
    return CoverDocumentScope(
        kind="catalogue", identity_id="alice",
        source_key="notice:alice-exact", provider_id="local",
    )


def test_request_live_predicate_respects_cancellation_and_fail_closed_errors():
    request = CoverReadRequest()
    assert request.is_current()  # can still show neutral absence
    state = [True]
    request._current_check = lambda: state[0]
    assert request.is_current()
    state[0] = False
    assert not request.is_current()
    request._current_check = lambda: 1  # strict boolean
    assert not request.is_current()
    request._current_check = lambda: 1 / 0
    assert not request.is_current()
    request.cancel()
    assert not request.is_current()


def test_replaced_n2_token_invalidates_queued_gtk_read_even_for_same_uri(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    loader = CoverLoader(max_workers=2)
    recorded = []
    fake_child = NS(cancel=lambda: recorded.append("cancel"))
    monkeypatch.setattr(
        loader, "load", lambda url, callback, **kwargs: fake_child,
    )
    service = DocumentCoverService(loader)
    try:
        scope = _scope()
        source = candidate(URI, tier="catalog", source="local",
                           evidence_id="notice-exact")
        service.register(CoverSourceSnapshot(
            scope=scope, revision=1, candidates=(source,),
        ))
        first = service.get_cover(scope, "detail", lambda path: None)
        assert first.source_uri == URI
        assert first.is_current()
        service.register(CoverSourceSnapshot(
            scope=scope, revision=2, candidates=(source,),
        ))
        # Same physical URL is NOT a reason to accept the old DocumentRef
        # revision or generation. Old worker callback cannot paint.
        assert not first.is_current()
        second = service.get_cover(scope, "detail", lambda path: None)
        assert second.is_current()
        assert second.source_uri == URI
        assert not first.is_current()
        service.decisions.invalidate(scope, cause="evidence_changed")
        assert not second.is_current()
    finally:
        service.close()
        loader.close()


def test_late_empty_placeholder_cannot_hide_new_n2_source(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    loader = CoverLoader(max_workers=2)
    service = DocumentCoverService(loader)
    try:
        scope = _scope()
        callbacks = []
        stale = service.get_cover(scope, "detail", callbacks.append)
        assert callbacks == [None]
        assert stale.source_uri is None
        assert stale.is_current()
        service.register(CoverSourceSnapshot(
            scope=scope, revision=1,
            candidates=(candidate(
                URI, tier="catalog", source="local",
                evidence_id="notice-exact",
            ),),
        ))
        assert not stale.is_current()  # GTK idle cannot hide newer source.
    finally:
        service.close()
        loader.close()


def test_replaced_n2_source_rejected_before_any_gtk_decode():
    host, loader, picture, calls, apply = env()
    token = [False]
    host._document_read_current = lambda: token[0]
    host._on_texture_applied = lambda *_: calls.append("paint_receipt")
    assert apply(host, 4, loader, Path("/tmp/old-source.png")) is False
    assert "stale" in calls
    assert "decode_attempt" not in calls
    assert "set_texture" not in calls
    assert "applied" not in calls
    assert "paint_receipt" not in calls
    assert picture.paintable is None


def test_n2_invalidated_during_gdk_decode_clears_texture_without_receipt():
    host, loader, picture, calls, apply = env()
    token = [True]
    host._document_read_current = lambda: token[0]
    origin = picture.set_paintable

    def invalidating_paint(texture):
        origin(texture)
        if texture is not None:
            token[0] = False  # N2 generation revoked between GDK and GTK.

    picture.set_paintable = invalidating_paint
    host._on_texture_applied = lambda *_: calls.append("paint_receipt")
    assert apply(host, 4, loader, Path("/tmp/old-source.png")) is False
    assert "decode_attempt" in calls
    assert "set_texture" in calls
    assert "stale" in calls
    assert "applied" not in calls  # no false N0/UI success
    assert "paint_receipt" not in calls
    assert picture.paintable is None


def test_unqualified_url_path_preserves_legacy_decode_semantics():
    host, loader, picture, calls, apply = env()
    host._document_read_current = None
    assert apply(host, 4, loader, Path("/tmp/public.png")) is False
    assert picture.paintable is not None
    assert calls[-1] == "applied"


def test_real_bind_document_captures_n2_predicate_before_idle_paint():
    source = (ROOT / "book_widgets.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    cover = next(x for x in tree.body if isinstance(x, ast.ClassDef)
                 and x.name == "CoverView")
    bind = next(x for x in cover.body if isinstance(x, ast.FunctionDef)
                and x.name == "bind_document")
    reset = next(x for x in cover.body if isinstance(x, ast.FunctionDef)
                 and x.name == "reset")
    paint = next(x for x in cover.body if isinstance(x, ast.FunctionDef)
                 and x.name == "_apply_result")
    assert "self._document_read_current = request.is_current" in ast.unparse(bind)
    assert "self._document_read_current = None" in ast.unparse(reset)
    paint_src = ast.unparse(paint)
    assert 'getattr(self, "_document_read_current", None)' in (
        paint_src.replace("'", '"')
    )
    assert paint_src.count("not current_n2()") == 2
    assert paint_src.index("not current_n2()") < paint_src.index(
        "Gdk.Texture.new_from_file"
    )
    assert paint_src.rindex("not current_n2()") < paint_src.index(
        "loader.note_ui_applied()"
    )
