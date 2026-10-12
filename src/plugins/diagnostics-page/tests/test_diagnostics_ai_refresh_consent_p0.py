"""P0 #1255: explicit Diagnostics refresh revokes previous AI consent.

Tests execute the same nested GTK callback bodies with pure doubles, without
network, PyGObject or a real LLM.
"""
from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import Mock


SOURCE = Path(__file__).resolve().parents[1] / "cifereca_diagnostics_page.py"


def _callback(name):
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    method = next(
        fn for fn in ast.walk(tree)
        if isinstance(fn, ast.FunctionDef) and fn.name == name
    )
    code = compile(
        ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])),
        str(SOURCE), "exec",
    )
    return code


def _exercise(name, read, *, expected_error=None):
    actions = []
    def invalidate_ai():
        actions.append("revoke")
    def render():
        actions.append("render")
        if read == "error":
            raise RuntimeError("fixture-broker-unavailable")
    def reset_events():
        actions.append("events")
    namespace = dict(
        invalidate_ai=invalidate_ai, render=render, reset_events=reset_events,
    )
    exec(_callback(name), namespace)
    callback = namespace[name]
    if expected_error is None:
        callback(None)
    else:
        try:
            callback(None)
        except expected_error:
            pass
        else:
            raise AssertionError("broker error was not propagated")
    return actions


def test_full_refresh_revokes_before_any_broker_read():
    assert _exercise("refresh_diagnostics", "ok") == [
        "revoke", "render", "events",
    ]


def test_failed_broker_refresh_cannot_preserve_old_consent():
    assert _exercise("refresh_diagnostics", "error", expected_error=RuntimeError) == [
        "revoke", "render",
    ]


def test_journals_refresh_revokes_before_fetching_new_events():
    assert _exercise("refresh_journals", "ok") == ["revoke", "events"]


def test_generation_invalidation_disables_send_and_ignores_stale_responses():
    code = SOURCE.read_text(encoding="utf-8")
    assert 'journals_refresh.connect("clicked", refresh_journals)' in code
    assert 'refresh.connect("clicked", refresh_diagnostics)' in code
    tree = ast.parse(code)
    invalidate = next(n for n in ast.walk(tree)
                      if isinstance(n, ast.FunctionDef) and n.name == "invalidate_ai")
    source = ast.unparse(invalidate)
    assert "ai_generation += 1" in source
    assert "ai_preview = None" in source
    assert "ai_send.set_sensitive(False)" in source
    assert "ai_input.get_buffer().set_text('')" in source
    assert "ai_report.get_buffer().set_text('')" in source
    for callback in ("finish_preview", "finish_ai"):
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == callback)
        assert "token != ai_generation" in ast.unparse(fn)


def test_refresh_never_prepares_or_sends_an_llm_request_implicitly():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    for callback in ("refresh_journals", "refresh_diagnostics"):
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == callback)
        source = ast.unparse(fn)
        assert "prepare_ai(" not in source
        assert "send_ai(" not in source
        assert "analyze_with_consent(" not in source
