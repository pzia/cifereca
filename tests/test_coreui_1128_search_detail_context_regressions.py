"""Behavioral regression coverage for late search-detail callbacks (#1128)."""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace


DETAILS = Path(__file__).resolve().parents[1] / "src/cifereca/gui/search_details.py"


def _context_checker():
    tree = ast.parse(DETAILS.read_text(encoding="utf-8"))
    method = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_search_detail_context_current"
    )
    namespace = {}
    exec(compile(ast.fix_missing_locations(ast.Module(
        body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), method],
        type_ignores=[],
    )), str(DETAILS), "exec"), namespace)
    return namespace["_search_detail_context_current"]


def _host():
    host = SimpleNamespace(_closed=False, _selected_search_id="book-A")
    host.context = (3, 7, 11, "identity-A")
    host._search_detail_request_context = lambda: host.context
    return host


def test_current_selection_and_context_accepts_detail():
    host = _host()
    assert _context_checker()(host, "book-A", host.context)


def test_closed_window_rejects_even_compatible_detail():
    host = _host()
    host._closed = True
    assert not _context_checker()(host, "book-A", host.context)
    assert not _context_checker()(host, "book-A", None)


def test_selection_change_rejects_reused_context():
    host = _host()
    assert not _context_checker()(host, "book-B", host.context)


def test_each_generation_and_identity_change_rejects_stale_detail():
    checker = _context_checker()
    for stale in ((2, 7, 11, "identity-A"), (3, 6, 11, "identity-A"),
                  (3, 7, 10, "identity-A"), (3, 7, 11, "identity-B")):
        assert not checker(_host(), "book-A", stale)


def test_none_context_remains_sync_compatibility_only_when_open_and_selected():
    host = _host()
    assert _context_checker()(host, "book-A", None)
    host._selected_search_id = "book-B"
    assert not _context_checker()(host, "book-A", None)
