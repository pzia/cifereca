"""#6168: a physical N3 callback can expire before GTK's idle paints pixels."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from cifereca.core.book_cover_projection import CoverDocumentScope
from cifereca.core.covers import candidate
from cifereca.gui.cover_loader import CoverLoader
from cifereca.gui.document_cover_service import CoverSourceSnapshot, DocumentCoverService
from tests.test_core_p1_6168_gtk_texture_applied_hook import env


PUBLIC = "https://public.example.org/edition.jpg"
PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 48


@pytest.fixture
def selected(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    loader = CoverLoader(max_workers=2)
    scope = CoverDocumentScope(
        kind="catalogue", identity_id="alice",
        source_key="provider:local/edition:42", provider_id="local",
    )
    known = candidate(PUBLIC, tier="catalog", source="local",
                      evidence_id="edition-42")
    snapshot = CoverSourceSnapshot(
        scope=scope, revision=1, candidates=(known,),
    )
    service = DocumentCoverService(loader)
    loader._path(PUBLIC).write_bytes(PNG)
    service.register(snapshot)
    try:
        yield loader, service, snapshot
    finally:
        service.close()
        loader.close()


def test_exact_selected_token_survives_n3_hit_but_not_n2_invalidation(selected):
    _loader, service, snapshot = selected
    calls = []
    request = service.get_cover(snapshot.scope, "detail", calls.append)
    assert request.source_uri == PUBLIC
    assert request.still_current
    assert len(calls) == 1
    assert calls[0] is not None
    service.decisions.invalidate(snapshot.scope, cause="evidence_changed")
    assert not request.still_current


def test_new_snapshot_invalidates_prior_request_even_with_same_image_uri(selected):
    _loader, service, snapshot = selected
    request = service.get_cover(snapshot.scope, "detail", lambda *_: None)
    assert request.still_current
    service.register(replace(snapshot, revision=2))
    assert not request.still_current
    newer = service.get_cover(snapshot.scope, "detail", lambda *_: None)
    assert newer.source_uri == PUBLIC and newer.still_current
    assert not request.still_current
    newer.cancel()
    assert not newer.still_current


def test_gtk_idle_before_decode_discards_old_source_without_claiming_paint():
    host, loader, picture, calls, apply = env()
    host._document_check = lambda: False
    path = Path("/tmp/valid-but-stale-n3-cover.png")
    assert apply(host, 4, loader, path) is False
    assert "stale" in calls
    assert "decode_attempt" not in calls
    assert "set_texture" not in calls
    assert picture.paintable is None


def test_gtk_idle_keeps_current_selection_and_legacy_url_unchanged():
    host, loader, picture, calls, apply = env()
    host._document_check = lambda: True
    assert apply(host, 4, loader, Path("/tmp/valid-cover.png")) is False
    assert "decoded" in calls and "applied" in calls
    assert picture.paintable is not None
    assert "stale" not in calls
    host2, loader2, picture2, calls2, apply2 = env()
    assert apply2(host2, 4, loader2, Path("/tmp/legacy-url.png")) is False
    assert picture2.paintable is not None
    assert "stale" not in calls2


def test_cover_view_binding_keeps_paint_gate_after_worker_callback():
    source = (Path(__file__).resolve().parents[1] /
              "src/cifereca/gui/book_widgets.py").read_text(encoding="utf-8")
    assert "self._document_check = lambda: request.still_current" in source
    assert "self._document_check = None" in source
    assert "check = getattr(self, \"_document_check\", None)" in source
    assert source.index('check = getattr(self, "_document_check", None)') < (
        source.index("texture = Gdk.Texture.new_from_file")
    )
