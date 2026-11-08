"""P1 #4996: a validated cover must reach the GTK main context before idle work.

Exercises the actual CoverView._start_cover_request, with an injected GLib
scheduler and no display, provider, filesystem, or real network. The existing
generation fence still rejects callbacks associated with a recycled cell.
"""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS

from tests.gui_source import gui_source_text

SOURCE = Path(__file__).resolve().parents[1] / "src/cifereca/gui/book_widgets.py"


def _actual_method(glib):
    tree = ast.parse(gui_source_text(SOURCE))
    owner = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "CoverView"
    )
    method = next(
        node for node in owner.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_start_cover_request"
    )
    module = ast.Module(
        body=[
            ast.ImportFrom(
                module="__future__", names=[ast.alias(name="annotations")],
                level=0,
            ),
            method,
        ],
        type_ignores=[],
    )
    scope = {"GLib": glib, "Path": Path}
    exec(compile(ast.fix_missing_locations(module), str(SOURCE), "exec"), scope)
    return scope["_start_cover_request"]


class FakeScheduler:
    PRIORITY_DEFAULT = 0
    PRIORITY_DEFAULT_IDLE = 200

    def __init__(self):
        self.pending = []

    def idle_add(self, callback, *args, priority=PRIORITY_DEFAULT_IDLE):
        self.pending.append((priority, callback, args))
        return len(self.pending)

    def run(self):
        for _, callback, args in sorted(self.pending, key=lambda row: row[0]):
            callback(*args)
        self.pending.clear()


class FakeLoader:
    def __init__(self):
        self.paths = []

    def load(self, url, received, *, priority, force):
        received(Path("/tmp/synthetic-cover.png"))
        self.paths.append((url, priority, force))
        return NS(cancel=lambda: None)


def _cover(loader):
    delivered = []
    cover = NS(
        _loader=loader, _url="synthetic-cover", _priority=10,
        _width=120, _height=180, _generation=1, _request=None,
        show_loading=lambda: None,
        _apply_result=lambda gen, incoming_loader, path:
            delivered.append((gen, incoming_loader, path)),
    )
    return cover, delivered


def test_cover_completion_uses_default_priority_not_starvable_idle_priority():
    scheduler = FakeScheduler()
    actual = _actual_method(scheduler)
    loader = FakeLoader()
    cover, delivered = _cover(loader)
    actual(cover, force=False)
    assert len(scheduler.pending) == 1
    assert scheduler.pending[0][0] == scheduler.PRIORITY_DEFAULT
    assert cover._request is not None
    assert delivered == []
    scheduler.run()
    assert delivered == [(1, loader, Path("/tmp/synthetic-cover.png"))]


def test_rebind_generation_is_preserved_and_not_overridden_by_dispatch():
    scheduler = FakeScheduler()
    actual = _actual_method(scheduler)
    loader = FakeLoader()
    cover, delivered = _cover(loader)
    actual(cover, force=False)
    cover._generation = 2
    actual(cover, force=True)
    assert len(scheduler.pending) == 2
    scheduler.run()
    # The real _apply_result keeps the generation fence; the dispatcher
    # must transmit the original generation unchanged for both callbacks.
    assert [generation for generation, _, _ in delivered] == [1, 2]
    assert loader.paths == [
        ("synthetic-cover", 10, False), ("synthetic-cover", 10, True)
    ]
