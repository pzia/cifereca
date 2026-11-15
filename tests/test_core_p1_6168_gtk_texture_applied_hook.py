"""#6168 N4: a cache hit is not a successfully painted GTK texture.

The real CoverView._apply_result is executed from production AST with fake
GDK/Picture: no GTK runtime, supplier I/O or image download is needed.
"""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS
import time

import pytest


ROOT = Path(__file__).resolve().parents[1] / "src/cifereca/gui/book_widgets.py"
PUBLIC = "https://covers.example.org/one.jpg"


class Loader:
    def __init__(self, calls):
        self.calls = calls
        self.diagnostics = None
    def note_ui_stale(self):
        self.calls.append("stale")
    def note_ui_failed(self):
        self.calls.append("failed")
    def note_ui_applied(self):
        self.calls.append("applied")
    def note_ui_decode(self, _duration):
        self.calls.append("decoded")
    def invalidate(self, _path, *, decode_failure=False):
        self.calls.append(("invalidate", decode_failure))


class Picture:
    def __init__(self, calls):
        self.paintable = None
        self.calls = calls
    def set_paintable(self, texture):
        self.calls.append("set_texture" if texture is not None else "clear_texture")
        self.paintable = texture
    def get_paintable(self):
        return self.paintable


def production_method(name, *, namespace=None):
    tree = ast.parse(ROOT.read_text(encoding="utf-8"))
    widget = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "CoverView"
    )
    func = next(
        node for node in widget.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )
    space = dict(namespace or {})
    exec(compile(ast.fix_missing_locations(ast.Module(
        body=[
            ast.ImportFrom(module="__future__", names=[
                ast.alias(name="annotations"),
            ], level=0),
            func,
        ], type_ignores=[],
    )), str(ROOT), "exec"), space)
    return space[name]


def env(*, fail_decode=False):
    calls = []
    image = object()
    picture = Picture(calls)
    loader = Loader(calls)
    def decode(_file):
        calls.append("decode_attempt")
        if fail_decode:
            raise ValueError("bad texture")
        return image
    method = production_method("_apply_result", namespace={
        "time": time,
        "Gdk": NS(Texture=NS(new_from_file=decode)),
        "Gio": NS(File=NS(new_for_path=lambda path: path)),
    })
    host = NS(
        _generation=4, _loader=loader, _url=PUBLIC,
        _width=60, _height=90,
        _decode_retry=True, _on_texture_applied=None,
        spinner=NS(stop=lambda: calls.append("stop_spinner"),
                   set_visible=lambda *_: None),
        placeholder=NS(set_visible=lambda *_: None),
        picture=picture,
        _set_logical_size=lambda *_: None,
        _show_missing=lambda: calls.append("missing"),
    )
    return host, loader, picture, calls, method


def test_actual_texture_callback_occurs_only_after_gtk_accepts_decoded_pixels():
    host, loader, picture, events, apply = env()
    observed = []
    def on_applied(url, path):
        observed.append((url, path, tuple(events)))
    host._on_texture_applied = on_applied
    actual_path = Path("/tmp/already-validated-cover.png")
    assert apply(host, 4, loader, actual_path) is False
    assert observed[0][0:2] == (PUBLIC, actual_path)
    assert "set_texture" in observed[0][2]
    assert "applied" in observed[0][2]
    assert observed[0][2].index("set_texture") < observed[0][2].index("applied")
    assert observed[0][2].index("applied") < len(observed[0][2])
    assert picture.paintable is not None


@pytest.mark.parametrize("generation,path", [
    (3, Path("/tmp/late.png")), (5, Path("/tmp/future.png")),
    (4, None),
])
def test_stale_generation_or_empty_result_never_claims_gtk_paint(
    generation, path,
):
    host, loader, picture, calls, apply = env()
    observed = []
    host._on_texture_applied = lambda *_args: observed.append("paint")
    assert apply(host, generation, loader, path) is False
    assert observed == []
    if generation != 4:
        assert "stale" in calls
    else:
        assert picture.paintable is None


def test_decode_exception_never_sends_successful_texture_event():
    host, loader, picture, calls, apply = env(fail_decode=True)
    observed = []
    host._on_texture_applied = lambda *_args: observed.append("paint")
    assert apply(host, 4, loader, Path("/tmp/bad.png")) is False
    assert "failed" in calls
    assert ("invalidate", True) in calls
    assert observed == []
    assert picture.paintable is None


def test_bad_core_observer_cannot_hide_valid_paint_or_restart_provider_io():
    host, loader, picture, calls, apply = env()
    observed = []
    def broken(_url, _path):
        observed.append("callback")
        raise RuntimeError("observer failed")
    host._on_texture_applied = broken
    assert apply(host, 4, loader, Path("/tmp/real.png")) is False
    assert observed == ["callback"]
    assert "applied" in calls
    assert "failed" not in calls
    assert picture.paintable is not None


def test_changed_cover_generation_while_painting_does_not_publish_old_lease():
    host, loader, picture, calls, apply = env()
    original = picture.set_paintable
    observed = []
    def reentered(texture):
        original(texture)
        host._generation += 1
    picture.set_paintable = reentered
    host._on_texture_applied = lambda *_: observed.append("paint")
    assert apply(host, 4, loader, Path("/tmp/ready.png")) is False
    assert observed == []
    assert "applied" in calls


def test_binding_changes_only_accept_callable_and_uses_current_request_generation():
    setter = production_method("set_texture_applied_observer")
    host = NS(_on_texture_applied="old")
    callback = lambda *_: None
    setter(host, callback)
    assert host._on_texture_applied is callback
    setter(host, "not callable")
    assert host._on_texture_applied is None
    setter(host, None)
    assert host._on_texture_applied is None


def test_real_cell_bind_and_thumbnail_signal_pass_same_optional_n0_hook():
    tree = ast.parse(ROOT.read_text(encoding="utf-8"))
    cell = next(
        x for x in tree.body
        if isinstance(x, ast.ClassDef) and x.name == "BookListCell"
    )
    for name in ("bind", "_thumbnail_changed"):
        method = next(
            x for x in cell.body
            if isinstance(x, ast.FunctionDef) and x.name == name
        )
        body = ast.unparse(method)
        assert 'on_texture_applied=getattr(item, \'_cover_paint_observer\', None)' in (
            body.replace('"', "'")
        )
    cover = next(x for x in tree.body if isinstance(x, ast.ClassDef)
                 and x.name == "CoverView")
    reset = next(x for x in cover.body if isinstance(x, ast.FunctionDef)
                 and x.name == "reset")
    bind = next(x for x in cover.body if isinstance(x, ast.FunctionDef)
                and x.name == "bind_url")
    assert "self._on_texture_applied = None" in ast.unparse(reset)
    assert "self._on_texture_applied =" in ast.unparse(bind)
    assert "self._generation" in ast.unparse(
        next(x for x in cover.body if isinstance(x, ast.FunctionDef)
             and x.name == "_apply_result")
    )


def test_no_observer_is_backward_compatible_with_existing_cover_bindings():
    host, loader, picture, calls, apply = env()
    assert host._on_texture_applied is None
    assert apply(host, 4, loader, Path("/tmp/existing.png")) is False
    assert picture.paintable is not None
    assert calls[-1] == "applied"
