"""P1 #3104/#774: a repeated GTK binding must not invalidate a live cover.

Exercise the production CoverView.bind_url method without importing GTK:
the method is extracted from the real CoreUI module and called with a
minimal deterministic widget double. No network, filesystem cache, or timers.
"""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

from tests.gui_source import gui_source_text


SOURCE = Path(__file__).resolve().parents[1] / "src/cifereca/gui/book_widgets.py"


def real_bind_url():
    tree = ast.parse(gui_source_text(SOURCE))
    owner = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "CoverView"
    )
    method = next(
        node for node in owner.body
        if isinstance(node, ast.FunctionDef) and node.name == "bind_url"
    )
    scope = {"CoverLoader": object}
    body = ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[]))
    exec(compile(body, str(SOURCE), "exec"), scope)
    return scope["bind_url"]


class CoverDouble:
    def __init__(self):
        self._loader = None
        self._url = None
        self._priority = 10
        self.loading = False
        self.resets = 0
        self.missing = 0
        self.started = []
        self.spinner = SimpleNamespace(get_visible=lambda: self.loading)
        self.paintable = None
        self.picture = SimpleNamespace(get_paintable=lambda: self.paintable)

    def reset(self):
        self.resets += 1
        self._loader = None
        self._url = None
        self.loading = False
        self.paintable = None

    def _show_missing(self):
        self.missing += 1

    def _start_cover_request(self, *, force):
        self.started.append((self._loader, self._url, self._priority, force))
        self.loading = True


def test_identical_pending_cover_rebind_preserves_generation_and_callback():
    bind = real_bind_url()
    loader = object()
    cover = CoverDouble()
    bind(cover, loader, "https://covers.example.test/a.png", priority=7)
    original = (cover.resets, list(cover.started))
    for _ in range(12):
        bind(cover, loader, "https://covers.example.test/a.png", priority=7)
    assert (cover.resets, cover.started) == original
    assert cover._url == "https://covers.example.test/a.png"
    assert cover.loading
    assert cover.missing == 0


def test_changed_url_loader_or_priority_still_cancels_previous_binding():
    bind = real_bind_url()
    cover = CoverDouble()
    original_loader, other_loader = object(), object()
    bind(cover, original_loader, "https://covers.example.test/a.png")
    assert cover.resets == 1
    bind(cover, original_loader, "https://covers.example.test/b.png")
    assert cover.resets == 2
    bind(cover, other_loader, "https://covers.example.test/b.png")
    assert cover.resets == 3
    bind(cover, other_loader, "https://covers.example.test/b.png", priority=5)
    assert cover.resets == 4
    assert len(cover.started) == 4
    assert cover.started[-1] == (
        other_loader, "https://covers.example.test/b.png", 5, False
    )


def test_completed_failed_and_absent_cover_keep_refresh_and_placeholder_contract():
    bind = real_bind_url()
    cover = CoverDouble()
    loader = object()
    bind(cover, loader, "https://covers.example.test/a.png")
    # A failed callback hides the spinner and has no texture, so an explicit
    # rebind may retry rather than preserving a failed result.
    cover.loading = False
    bind(cover, loader, "https://covers.example.test/a.png")
    assert cover.resets == 2
    assert len(cover.started) == 2
    bind(cover, loader, None)
    assert cover.resets == 3
    assert cover.missing == 1
    assert not cover.loading
    assert cover._url is None


def test_identical_rendered_cover_rebind_never_blanks_texture_or_reloads():
    """#4996: local GTK redraw must preserve a successfully painted URL."""
    bind = real_bind_url()
    loader = object()
    cover = CoverDouble()
    bind(cover, loader, "https://covers.example.test/a.png", priority=7)
    cover.loading = False
    cover.paintable = object()
    original = (cover.resets, list(cover.started), cover.paintable)
    for _ in range(20):
        bind(cover, loader, "https://covers.example.test/a.png", priority=7)
    assert (cover.resets, cover.started, cover.paintable) == original


def test_completed_texture_still_invalidated_for_different_source():
    """A cached texture must never survive a changed URL/loader/priority."""
    bind = real_bind_url()
    cover = CoverDouble()
    loader, other = object(), object()
    bind(cover, loader, "https://covers.example.test/a.png")
    for params in (
        (loader, "https://covers.example.test/b.png", 10),
        (other, "https://covers.example.test/b.png", 10),
        (other, "https://covers.example.test/b.png", 4),
    ):
        cover.loading = False
        cover.paintable = object()
        before = cover.resets
        bind(cover, params[0], params[1], priority=params[2])
        assert cover.resets == before + 1
        assert cover.paintable is None
