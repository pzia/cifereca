"""UX P0 #1009 — consent and evidence contract for optional Diagnostics AI.

Static characterization: expected gaps are strict xfail until implemented.
These tests are not a real GTK acceptance run and do not access a real LLM.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest


SOURCE = Path(__file__).resolve().parents[1] / "src/plugins/diagnostics-page/cifereca_diagnostics_page.py"


def _build() -> ast.FunctionDef:
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    plugin = next(node for node in tree.body
                  if isinstance(node, ast.ClassDef) and node.name == "DiagnosticsPagePlugin")
    return next(node for node in plugin.body
                if isinstance(node, ast.FunctionDef) and node.name == "_build")


def _source() -> str:
    return ast.get_source_segment(SOURCE.read_text(encoding="utf-8"), _build()) or ""


@pytest.mark.xfail(strict=True, reason="#1009: user-triggered AI action not implemented")
def test_1009_analyze_button_exists_but_is_user_triggered():
    source = _source()
    assert "Analyser avec l’IA" in source or "Analyser avec l'IA" in source
    assert "clicked" in source, "Run AI only after an explicit user action"


@pytest.mark.xfail(strict=True, reason="#1009: preflight preview and consent missing")
def test_1009_remote_analysis_requires_preview_and_explicit_consent():
    source = _source().casefold()
    assert "prévisualis" in source or "aperçu" in source
    assert "consent" in source or "confirmer" in source
    assert "annul" in source, "User must be able to cancel before sending"


@pytest.mark.xfail(strict=True, reason="#1009: generated findings must be labeled distinctly")
def test_1009_generated_report_cannot_masquerade_as_deterministic_evidence():
    source = _source().casefold()
    assert "généré" in source or "hypothèse ia" in source
    assert "rapport support" in source
    assert "source" in source, "AI findings must preserve evidence provenance"


def test_1009_initial_map_does_not_call_llm_directly():
    build = _build()
    calls = [
        node for node in ast.walk(build)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"complete", "generate", "infer", "chat"}
    ]
    assert not calls, "No implicit LLM execution while building the page"


def test_1009_plugin_does_not_read_private_files():
    build = _build()
    calls = [
        node for node in ast.walk(build)
        if isinstance(node, ast.Call) and (
            isinstance(node.func, ast.Name) and node.func.id == "open"
            or isinstance(node.func, ast.Attribute)
            and node.func.attr in {"read_text", "read_bytes", "rglob", "glob"}
        )
    ]
    assert not calls, "Logs must be obtained from a safe public Core broker"
