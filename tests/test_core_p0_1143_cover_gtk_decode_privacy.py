"""P0 #1143: real CoverView._apply_result code, exercised without PyGObject.

Compile only the GTK-independent method AST against stub Gdk/Gio objects,
preserving the real generation/retry/invalidation code. No pixels, HTTP,
provider account, GTK display, or private local file is read.
"""
from __future__ import annotations

import ast
import json
import time
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from cifereca.diagnostics import Diagnostics


SOURCE = Path(__file__).resolve().parents[1] / "src/cifereca/gui/book_widgets.py"
SECRET = "https://private.invalid/LOAN/borrow?auth=SECRET_COVER_DECODE"
PRIVATE_PATH = Path("/home/private-reader/SECRET_COVER_DECODE.jpg")


def _apply_result(*, decoder):
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    cover = next(node for node in tree.body
                 if isinstance(node, ast.ClassDef) and node.name == "CoverView")
    method = next(node for node in cover.body
                  if isinstance(node, ast.FunctionDef) and node.name == "_apply_result")
    module = ast.Module(body=[
        ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
        method,
    ], type_ignores=[])
    namespace = {
        "time": time,
        "Gdk": NS(Texture=NS(new_from_file=decoder)),
        "Gio": NS(File=NS(new_for_path=lambda path: path)),
        "Path": Path,
    }
    exec(compile(ast.fix_missing_locations(module), str(SOURCE), "exec"), namespace)
    return namespace["_apply_result"]


def _harness():
    diagnostics = Diagnostics(level="detailed")
    loader = NS(
        diagnostics=diagnostics,
        note_ui_failed=Mock(), note_ui_decode=Mock(), note_ui_applied=Mock(),
        note_ui_stale=Mock(), invalidate=Mock(),
    )
    cover = NS(
        _generation=11,
        _width=60, _height=90,
        _decode_retry=False, _url=SECRET,
        _loader=loader,
        spinner=NS(stop=Mock(), set_visible=Mock()),
        picture=NS(set_paintable=Mock()),
        placeholder=NS(set_visible=Mock()),
        _set_logical_size=Mock(), _start_cover_request=Mock(),
        _show_missing=Mock(),
    )
    return cover, loader


@pytest.mark.parametrize("mode", ["private_path", "hostile_str"])
def test_gtk_decode_failure_uses_only_closed_diagnostic_and_one_retry(mode):
    class Hostile(RuntimeError):
        def __str__(self):
            raise AssertionError("Must not stringify GTK exception")
        def __repr__(self):
            raise AssertionError("Must not repr GTK exception")

    def decode(_path):
        if mode == "private_path":
            raise RuntimeError(str(PRIVATE_PATH) + " " + SECRET)
        raise Hostile(SECRET)

    apply = _apply_result(decoder=decode)
    cover, loader = _harness()

    assert apply(cover, 11, loader, PRIVATE_PATH) is False
    loader.note_ui_failed.assert_called_once()
    loader.invalidate.assert_called_once_with(PRIVATE_PATH, decode_failure=True)
    cover._start_cover_request.assert_called_once_with(force=True)
    assert cover._decode_retry is True

    rows = loader.diagnostics.snapshot()
    assert len(rows) == 1
    assert rows[0]["category"] == "COVER"
    assert rows[0]["operation"] == "ui decode"
    assert rows[0]["status"] == "error"
    assert rows[0]["detail"] == {"code": "cover_decode_failed"}
    exported = json.dumps(rows) + loader.diagnostics.text("test")
    assert "SECRET_COVER_DECODE" not in exported
    assert "/home/private-reader" not in exported
    assert "private.invalid" not in exported

    # The old retry policy must not create an unbounded decode loop.
    assert apply(cover, 11, loader, PRIVATE_PATH) is False
    loader.invalidate.assert_called_with(PRIVATE_PATH, decode_failure=True)
    assert loader.invalidate.call_count == 2
    cover._start_cover_request.assert_called_once()
    cover._show_missing.assert_called_once()


def test_stale_cover_decode_result_is_ignored_without_logging_or_retry():
    apply = _apply_result(decoder=lambda _path: pytest.fail("stale texture decoded"))
    cover, loader = _harness()
    assert apply(cover, 10, loader, PRIVATE_PATH) is False
    loader.note_ui_stale.assert_called_once()
    loader.note_ui_failed.assert_not_called()
    loader.invalidate.assert_not_called()
    cover._start_cover_request.assert_not_called()
    assert loader.diagnostics.snapshot() == []


def test_successful_cover_decode_keeps_original_texture_path():
    texture = object()
    apply = _apply_result(decoder=lambda source: texture)
    cover, loader = _harness()
    assert apply(cover, 11, loader, PRIVATE_PATH) is False
    cover.picture.set_paintable.assert_called_once_with(texture)
    loader.note_ui_applied.assert_called_once()
    loader.note_ui_decode.assert_called_once()
    loader.note_ui_failed.assert_not_called()
    loader.invalidate.assert_not_called()
    cover._start_cover_request.assert_not_called()
    assert loader.diagnostics.snapshot() == []
