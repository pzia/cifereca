"""#6168: final N2 CAS gate after GDK decoding + stale missing placeholders.

Existing MainWindow/CoverView already check N2 BEFORE native decode. This
tranche adds the missing final post-decode check without changing the public
DetailPanel, N2 source selection or profile-independent N3 caches.
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
    CoverSourceSnapshot, DocumentCoverService,
)
from tests.test_core_p1_6168_gtk_texture_applied_hook import env

ROOT = Path(__file__).resolve().parents[1] / "src/cifereca/gui"
URI = "https://public.example.org/book.png"


def _scope():
    return CoverDocumentScope(
        kind="catalogue", identity_id="alice",
        source_key="notice:exact", provider_id="local",
    )


def test_old_n2_token_rejected_even_when_new_revision_has_same_image_url(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    loader = CoverLoader(max_workers=2)
    monkeypatch.setattr(
        loader, "load", lambda *_args, **_kw: NS(cancel=lambda: None),
    )
    service = DocumentCoverService(loader)
    try:
        scope = _scope()
        source = candidate(
            URI, tier="catalog", source="local",
            evidence_id="edition:verified",
        )
        service.register(CoverSourceSnapshot(
            scope=scope, revision=1, candidates=(source,),
        ))
        one = service.get_cover(scope, "detail", lambda _path: None)
        assert one.source_uri == URI and one.still_current
        service.register(CoverSourceSnapshot(
            scope=scope, revision=2, candidates=(source,),
        ))
        assert not one.still_current  # same URI is NOT a valid N2 token
        two = service.get_cover(scope, "detail", lambda _path: None)
        assert two.still_current
        service.decisions.invalidate(scope, cause="evidence_changed")
        assert not two.still_current
    finally:
        service.close()
        loader.close()


def test_old_empty_read_cannot_repaint_after_real_n2_source_arrived(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    loader = CoverLoader(max_workers=2)
    service = DocumentCoverService(loader)
    try:
        scope = _scope()
        deliveries = []
        missing = service.get_cover(scope, "detail", deliveries.append)
        assert deliveries == [None] and missing.still_current
        assert missing.source_uri is None
        service.register(CoverSourceSnapshot(
            scope=scope, revision=1,
            candidates=(candidate(
                URI, tier="catalog", source="local",
                evidence_id="notice:1",
            ),),
        ))
        assert not missing.still_current
    finally:
        service.close()
        loader.close()


def test_gtk_rejects_invalid_n2_before_native_decode_and_shows_placeholder():
    host, loader, picture, events, apply = env()
    host._document_check = lambda: False
    host._on_texture_applied = lambda *_args: events.append("receipt")
    assert apply(host, 4, loader, Path("/tmp/cached-old.png")) is False
    assert picture.paintable is None
    assert "stale" in events
    assert "decode_attempt" not in events
    assert "set_texture" not in events
    assert "applied" not in events
    assert "receipt" not in events


def test_gtk_rechecks_n2_after_texture_before_success_metric():
    host, loader, picture, events, apply = env()
    # env() stubs _show_missing as an event only; the real CoverView also
    # clears GDK's paintable. Model that UI side effect explicitly here.
    host._show_missing = lambda: picture.set_paintable(None)
    current = [True]
    host._document_check = lambda: current[0]
    host._on_texture_applied = lambda *_args: events.append("receipt")
    setter = picture.set_paintable

    def invalidate_during_gdk(texture):
        setter(texture)
        if texture is not None:
            current[0] = False

    picture.set_paintable = invalidate_during_gdk
    assert apply(host, 4, loader, Path("/tmp/stale.png")) is False
    assert "decode_attempt" in events
    assert "set_texture" in events
    assert "stale" in events
    assert "applied" not in events
    assert "receipt" not in events
    assert picture.paintable is None


def test_url_only_path_keeps_prior_success_and_n0_observer_order():
    host, loader, picture, events, apply = env()
    host._document_check = None
    applied = []
    host._on_texture_applied = lambda *_: applied.append("receipt")
    assert apply(host, 4, loader, Path("/tmp/valid.png")) is False
    assert picture.paintable is not None
    assert events.index("set_texture") < events.index("applied")
    assert applied == ["receipt"]


def test_real_gtk_sink_preserves_three_n2_checks_at_decode_repair_and_paint():
    source = (ROOT / "book_widgets.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    cover = next(c for c in tree.body if isinstance(c, ast.ClassDef)
                 and c.name == "CoverView")
    paint = next(fn for fn in cover.body
                 if isinstance(fn, ast.FunctionDef) and fn.name == "_apply_result")
    text = ast.unparse(paint)
    assert "getattr(self, '_document_check', None)" in text
    # The decode-failure path gained its own N2/CAS veto before a bounded
    # repair callback. That is a *third* protection, not a removed guard.
    # Pin all three boundaries instead of failing on the old count of two.
    checks = "current = check() is True"
    assert text.count(checks) == 3
    before_decode = text.index(checks)
    during_repair = text.index(checks, before_decode + len(checks))
    after_decode = text.rindex(checks)
    decode = text.index("Gdk.Texture.new_from_file")
    repair = text.index("repair = getattr(self, '_document_decode_retry', None)")
    paint_success = text.index("loader.note_ui_applied()")
    assert before_decode < decode < during_repair < repair
    assert during_repair < after_decode < paint_success
