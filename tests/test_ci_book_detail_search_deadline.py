"""#4980: the GTK Search budget begins on actual dispatch, not probe startup.

A slow GTK bootstrap is not evidence that the asynchronous local query
failed its twelve-second contract. The search result/count assertions and
the exact twelve-second bound are unchanged.
"""
from __future__ import annotations

import ast
from pathlib import Path


PROBE = Path(__file__).resolve().parents[1] / "e2e/book_detail_probe.py"


def _nested_main_step(tree, name):
    main = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "main"
    )
    return next(
        node for node in main.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )


def test_wait_search_starts_at_actual_dispatch_and_keeps_strict_12_seconds():
    tree = ast.parse(PROBE.read_text(encoding="utf-8"))
    initial = _nested_main_step(tree, "search_initial")
    waiting = _nested_main_step(tree, "wait_search")

    # The clock must be set just before the asynchronous Search call.
    dispatch = [
        index for index, node in enumerate(initial.body)
        if isinstance(node, ast.Expr)
        and ast.unparse(node.value) == "window.run_search(False)"
    ]
    assert len(dispatch) == 1
    start = initial.body[dispatch[0] - 1]
    assert isinstance(start, ast.Assign)
    assert len(start.targets) == 1
    assert ast.unparse(start.targets[0]) == "search_started[0]"
    assert ast.unparse(start.value) == "monotonic()"

    retry = waiting.body[0]
    assert isinstance(retry, ast.If)
    condition = ast.unparse(retry.test)
    assert "window.search_page.store.get_n_items() != 1" in condition
    assert "monotonic() - search_started[0] < 12" in condition
    assert "monotonic() - started" not in condition
    assert any(
        isinstance(node, ast.Assert)
        and "get_n_items() == 1" in ast.unparse(node.test)
        for node in waiting.body
    )
