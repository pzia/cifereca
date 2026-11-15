"""P1 #6168 N0: real GTK texture path emits one optional safe paint receipt.

The production CoverView methods are extracted by the existing headless
harness; no network, window, Gtk runtime or user catalogue is involved.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.test_p1_3104_inflight_cover_binding import (
    CoverDouble, real_bind_url,
)
from tests.test_core_p0_1143_cover_gtk_decode_privacy import (
    _apply_result, _harness, PRIVATE_PATH,
)

BOOKS = Path(__file__).resolve().parents[1] / "src/cifereca/gui/book_widgets.py"
DETAIL = Path(__file__).resolve().parents[1] / "src/cifereca/gui/detail_panel.py"


def _method(path, name, cls):
    root = ast.parse(path.read_text(encoding="utf-8"))
    owner = next(n for n in root.body
                 if isinstance(n, ast.ClassDef) and n.name == cls)
    return next(n for n in owner.body
                if isinstance(n, ast.FunctionDef) and n.name == name)


def test_successful_gtk_texture_emits_exactly_one_receipt_after_paintable():
    recorded = []
    texture = object()
    apply = _apply_result(decoder=lambda _source: texture)
    cover, loader = _harness()
    def applied():
        assert cover.picture.set_paintable.call_args.args == (texture,)
        assert loader.note_ui_applied.call_count == 1
        recorded.append("applied")
    cover._paint_observer = applied
    assert apply(cover, 11, loader, PRIVATE_PATH) is False
    assert recorded == ["applied"]
    loader.note_ui_failed.assert_not_called()
    loader.invalidate.assert_not_called()


def test_stale_or_failed_texture_never_emits_a_paint_receipt():
    seen = []
    cover, loader = _harness()
    cover._paint_observer = lambda: seen.append("unsafe")
    stale = _apply_result(decoder=lambda _path: pytest.fail("stale decode"))
    assert stale(cover, 10, loader, PRIVATE_PATH) is False
    assert seen == []
    failed = _apply_result(decoder=lambda _path: (_ for _ in ()).throw(
        ValueError("unreadable PNG")
    ))
    assert failed(cover, 11, loader, PRIVATE_PATH) is False
    assert seen == []
    loader.note_ui_failed.assert_called_once()
    loader.invalidate.assert_called_once()


def test_broken_observer_is_not_misclassified_as_gdk_decode_failure():
    cover, loader = _harness()
    def broken():
        raise RuntimeError("test observer failed")
    cover._paint_observer = broken
    success = _apply_result(decoder=lambda _path: object())
    assert success(cover, 11, loader, PRIVATE_PATH) is False
    loader.note_ui_applied.assert_called_once()
    loader.note_ui_failed.assert_not_called()
    loader.invalidate.assert_not_called()
    cover._start_cover_request.assert_not_called()


def test_rebound_same_url_with_different_producer_cancelled_and_restarted():
    bind = real_bind_url()
    loader = object()
    cover = CoverDouble()
    a = lambda: None
    b = lambda: None
    bind(cover, loader, "https://covers.example.org/exact.png",
         on_image_applied=a)
    assert cover.resets == 1 and cover._paint_observer is a
    bind(cover, loader, "https://covers.example.org/exact.png",
         on_image_applied=a)
    assert cover.resets == 1  # same current in-flight binding
    bind(cover, loader, "https://covers.example.org/exact.png",
         on_image_applied=b)
    assert cover.resets == 2  # same URL, DIFFERENT documentary producer
    assert cover._paint_observer is b
    bind(cover, loader, None)
    assert cover.resets == 3 and cover._paint_observer is None


def test_invalid_paint_observer_refused_without_cancelling_valid_binding():
    bind = real_bind_url()
    loader = object()
    cover = CoverDouble()
    bind(cover, loader, "https://covers.example.org/ok.png")
    with pytest.raises(TypeError):
        bind(cover, loader, "https://covers.example.org/other.png",
             on_image_applied=object())
    assert cover.resets == 1
    assert cover._url == "https://covers.example.org/ok.png"


def test_recycled_list_cell_cannot_acknowledge_wrong_book_or_epoch():
    owner = _method(BOOKS, "_cover_image_applied", "BookListCell")
    scope = {"BookListItem": object}
    exec(compile(ast.fix_missing_locations(
        ast.Module(body=[owner], type_ignores=[]),
    ), str(BOOKS), "exec"), scope)
    invoked = []
    row = type("Row", (), {})()
    row.key = "loan-source-a"
    row._on_cover_applied = lambda: invoked.append("correct")
    cell = type("Cell", (), {})()
    cell._thumbnail_item = row
    cell._bound_key = row.key
    cell._bind_epoch = 7
    scope[owner.name](cell, row, 7)
    assert invoked == ["correct"]
    cell._bind_epoch = 8
    scope[owner.name](cell, row, 7)
    cell._bind_epoch = 7
    cell._bound_key = "loan-source-b"
    scope[owner.name](cell, row, 7)
    assert invoked == ["correct"]


def test_list_and_detail_forward_only_bound_paint_callbacks():
    bind = ast.unparse(_method(BOOKS, "bind", "BookListCell"))
    changed = ast.unparse(_method(BOOKS, "_thumbnail_changed", "BookListCell"))
    reset = ast.unparse(_method(BOOKS, "reset", "CoverView"))
    original = ast.unparse(_method(BOOKS, "_apply_result", "CoverView"))
    assert "self._cover_image_applied" in bind
    assert "on_image_applied=on_applied" in bind
    assert "on_image_applied=callback" in changed
    assert "self._paint_observer = None" in reset
    assert "observer()" in original
    assert original.index("loader.note_ui_applied()") < original.index("observer()")
    detail_cover = ast.unparse(_method(DETAIL, "_cover_and_title", "DetailPanel"))
    loan = ast.unparse(_method(DETAIL, "show_loan", "DetailPanel"))
    assert 'on_image_applied=getattr(self, "_cover_image_applied_callback", None)' in detail_cover
    assert 'getattr(loan, "_on_cover_applied", None)' in loan
    assert "finally:" in loan
    assert "self._cover_image_applied_callback = None" in loan
