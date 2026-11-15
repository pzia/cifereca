"""P1 #6168: real GTK cell methods bind the N2 ref, not a cached URL."""
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

ROOT = Path(__file__).resolve().parents[1] / "src/cifereca/gui/book_widgets.py"
URI = "https://covers.example.org/known.png"
PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 40


def method(class_name, name, *, namespace=None):
    tree = ast.parse(ROOT.read_text(encoding="utf-8"))
    cls = next(x for x in tree.body if isinstance(x, ast.ClassDef) and x.name == class_name)
    func = next(x for x in cls.body if isinstance(x, ast.FunctionDef) and x.name == name)
    ns = {"__name__": "cifereca.gui.book_widgets", "__package__": "cifereca.gui"}
    ns.update(namespace or {})
    exec(compile(ast.fix_missing_locations(ast.Module(body=[
        ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
        func,
    ], type_ignores=[])), str(ROOT), "exec"), ns)
    return ns[name]


@pytest.fixture
def registered(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    loader = CoverLoader(max_workers=2)
    source = loader._path(URI)
    source.write_bytes(PNG)
    scope = CoverDocumentScope(
        kind="catalogue", identity_id="alice",
        source_key="source:verified:one", provider_id="local",
    )
    bridge = DocumentCoverService(loader)
    bridge.register(CoverSourceSnapshot(
        scope=scope, revision=1,
        candidates=(candidate(URI, tier="catalog", source="local", evidence_id="one"),),
    ))
    try:
        yield loader, bridge, scope, source
    finally:
        bridge.close()
        loader.close()


def test_coverview_uses_document_n2_uri_and_gtk_idle_for_actual_paint(registered):
    loader, bridge, scope, source = registered
    calls = []
    idle = []

    def reset():
        host._generation += 1
        old = host._request
        if old is not None:
            old.cancel()
        host._request = None
        host._url = None

    def apply(_generation, _loader, _path):
        calls.append((_generation, _loader, _path))
        return False

    host = NS(
        _generation=0, _request=None, _url=None, _loader=None,
        _width=112, _height=168, reset=reset,
        _priority=0, show_loading=lambda: calls.append("loading"),
        get_scale_factor=lambda: 1, _apply_result=apply,
    )
    GLib = NS(
        PRIORITY_DEFAULT=0,
        idle_add=lambda fn, *args, **kwargs: idle.append((fn, args, kwargs)),
    )
    bind = method("CoverView", "bind_document", namespace={"GLib": GLib})
    bind(host, bridge, scope, variant="detail", priority=0)
    assert host._loader is loader
    assert host._url == URI  # only from live N2, never from cached filename.
    assert "loading" in calls
    assert len(idle) == 1
    fn, args, kw = idle[0]
    assert args == (1, loader, source)
    assert kw == {"priority": 0}
    fn(*args)
    assert calls[-1] == (1, loader, source)


def test_recycled_coverview_old_document_callback_is_generation_guarded(registered):
    _loader, bridge, scope, _source = registered
    queued = []
    resets = []

    def reset():
        host._generation += 1
        host._request = None
        host._url = None
        resets.append(host._generation)

    host = NS(
        _generation=0, _request=None, _url=None, _loader=None,
        _width=112, _height=168, reset=reset, _priority=0,
        show_loading=lambda: None, get_scale_factor=lambda: 1,
        _apply_result=lambda *args: queued.append(("paint", args)),
    )
    GLib = NS(
        PRIORITY_DEFAULT=0,
        idle_add=lambda fn, *args, **kwargs: queued.append(("idle", fn, args)),
    )
    bind = method("CoverView", "bind_document", namespace={"GLib": GLib})
    bind(host, bridge, scope, variant="detail")
    assert queued[0][0] == "idle"
    reset()
    stale = queued[0]
    assert stale[2][0] != host._generation
    # Real _apply_result has its generation guard tested separately in
    # test_core_p1_6168_gtk_texture_applied_hook.py.
    assert resets == [1, 2]


def test_booklistcell_thumbnail_change_prioritizes_registered_documentref():
    scope = CoverDocumentScope(
        kind="placement", identity_id="alice", source_key="place:a",
        artifact_id="sha256:" + "a" * 64, placement_id="place:a",
    )
    bindings = []
    cover = NS(
        bind_document=lambda *args, **kwargs: bindings.append(("doc", args, kwargs)),
        bind_url=lambda *args, **kwargs: bindings.append(("url", args, kwargs)),
    )
    bridge = object()
    item = NS(
        key="book:a", _cover_document_ref=scope, thumbnail_url=URI,
        default_thumbnail_url=None, _cover_paint_observer=None,
    )
    host = NS(
        _thumbnail_item=item, _bound_key=item.key,
        window=NS(document_covers=bridge), cover=cover, cover_loader=object(),
        # Production cells own the lazy Core-proof admission gate. This
        # legacy no-packet fixture already has a registered DocumentRef;
        # its fake must expose that method to the replayed GTK binder.
        _admit_core_local_cover=lambda *_args: True,
    )
    changed = method("BookListCell", "_thumbnail_changed")
    changed(host, item)
    assert len(bindings) == 1 and bindings[0][0] == "doc"
    assert bindings[0][1] == (bridge, scope)
    assert bindings[0][2]["variant"] == "list"
    item._cover_document_ref = None
    changed(host, item)
    assert len(bindings) == 2 and bindings[-1][0] == "url"


def test_genuine_list_bind_document_path_is_conditional_not_url_inference():
    tree = ast.parse(ROOT.read_text(encoding="utf-8"))
    book = next(x for x in tree.body if isinstance(x, ast.ClassDef) and x.name == "BookListCell")
    method_bind = next(x for x in book.body if isinstance(x, ast.FunctionDef) and x.name == "bind")
    source = ast.unparse(method_bind).replace('"', "'")
    assert "getattr(item, '_cover_document_ref', None)" in source
    assert "getattr(self.window, 'document_covers', None)" in source
    assert 'self.cover.bind_document(' in source
    assert 'elif item.thumbnail_url:' in source
    assert 'self.cover.bind_url(' in source


def test_mainwindow_owns_shared_document_cache_and_disposes_before_n3():
    root = ROOT.parent
    start = (root / "main_window.py").read_text(encoding="utf-8")
    close = (root / "window_close.py").read_text(encoding="utf-8")
    assert "self.document_covers = DocumentCoverService(" in start
    assert "cached_source_reader=lambda ref: cached_notice_cover_snapshot(" in start
    assert "self.service, ref" in start
    assert "service.close()" in close
    assert close.index("service.close()") < close.index("window.covers.close()")
