"""CoreUI P0 #1129 — last requested Library/Recent refresh owns GTK state.

Use the actual MainWindow method closures under existing headless fixture.
The older callback may finish after a second user-triggered refresh.
"""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from tests.test_core_ui_identity_async_recent import _harness, _change_identity


@pytest.mark.parametrize("method,shown,pending", [
    ("refresh_library", "library_shown", "_library_refresh_pending"),
    ("refresh_recent", "recent_shown", "_recent_refresh_pending"),
])
def test_overlapping_manual_refresh_last_success_wins(method, shown, pending):
    host = _harness()
    getattr(host, method)()
    first = host.runner.requests[-1]
    getattr(host, method)(control=object())
    second = host.runner.requests[-1]
    assert len(host.runner.requests) == 2
    first.ok([{"title": "older"}])
    assert getattr(host, shown) == []
    assert getattr(host, pending) is True
    second.ok([{"title": "newest"}])
    assert getattr(host, shown) == [[{"title": "newest"}]]
    assert getattr(host, pending) is False


@pytest.mark.parametrize("method,shown,pending", [
    ("refresh_library", "library_shown", "_library_refresh_pending"),
    ("refresh_recent", "recent_shown", "_recent_refresh_pending"),
])
def test_stale_error_does_not_show_toast_or_clear_newer_pending(method, shown, pending):
    host = _harness()
    getattr(host, method)(control=object())
    older = host.runner.requests[-1]
    getattr(host, method)(control=object())
    newer = host.runner.requests[-1]
    older.err(RuntimeError("stale private error"))
    assert getattr(host, shown) == []
    assert getattr(host, pending) is True
    assert len(host.runner.requests) == 2
    newer.ok([{"title": "current"}])
    assert getattr(host, shown) == [[{"title": "current"}]]


@pytest.mark.parametrize("method,shown", [
    ("refresh_library", "library_shown"),
    ("refresh_recent", "recent_shown"),
])
def test_late_completion_after_shutdown_does_not_reopen_or_schedule(method, shown):
    host = _harness()
    getattr(host, method)(control=object())
    old = host.runner.requests[-1]
    host._closed = True
    old.ok([{"private": True}])
    old.err(RuntimeError("private"))
    assert getattr(host, shown) == []
    assert len(host.runner.requests) == 1


def test_identity_change_reschedules_exactly_once_if_no_newer_request_exists():
    host = _harness()
    host.refresh_library()
    old = host.runner.requests[-1]
    _change_identity(host, "bob")
    old.ok([{"identity_id": "alice"}])
    assert len(host.runner.requests) == 2
    host.runner.requests[-1].ok([{"identity_id": "bob"}])
    assert host.library_shown == [[{"identity_id": "bob"}]]


def test_generations_are_separate_for_library_and_recent():
    host = _harness()
    host.refresh_library()
    host.refresh_recent()
    assert host._library_refresh_generation == 1
    assert host._recent_refresh_generation == 1
    host.refresh_library(control=object())
    assert host._library_refresh_generation == 2
    assert host._recent_refresh_generation == 1


def test_both_callbacks_guard_before_clearing_pending_or_writing():
    path = Path(__file__).resolve().parents[1] / "src/cifereca/gui/main_window.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    window = next(node for node in tree.body
                  if isinstance(node, ast.ClassDef) and node.name == "MainWindow")
    for name, generation in (("refresh_library", "_library_refresh_generation"),
                             ("refresh_recent", "_recent_refresh_generation")):
        method = next(node for node in window.body
                      if isinstance(node, ast.FunctionDef) and node.name == name)
        body = ast.unparse(method)
        assert f"request_generation != self.{generation}" in body
        assert "getattr(self, '_closed', False)" in body
        assert "request_generation =" in body
        for callback in ("ok", "err"):
            fn = next(node for node in method.body
                      if isinstance(node, ast.FunctionDef) and node.name == callback)
            lines = ast.unparse(fn)
            assert lines.index("request_generation != self.") < lines.index("_refresh_pending = False")
