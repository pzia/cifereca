"""UX P0 #1009 — consent and evidence contract for optional Diagnostics AI.

Static UI contract complements the real GTK consent/LLM tests.
No LLM is invoked here; xfail was an obsolete button-label assertion.
"""
from __future__ import annotations

import ast
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "src/plugins/diagnostics-page/cifereca_diagnostics_page.py"


def _build() -> ast.FunctionDef:
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    plugin = next(node for node in tree.body
                  if isinstance(node, ast.ClassDef) and node.name == "DiagnosticsPagePlugin")
    return next(node for node in plugin.body
                if isinstance(node, ast.FunctionDef) and node.name == "_build")


def _source() -> str:
    return ast.get_source_segment(SOURCE.read_text(encoding="utf-8"), _build()) or ""


def test_1009_analyze_button_exists_but_is_user_triggered():
    source = _source()
    assert 'Gtk.Button(label="Prévisualiser pour l’IA")' in source
    assert 'Gtk.Button(label="Autoriser et analyser")' in source
    assert 'ai_send.set_sensitive(False)' in source
    assert 'ai_prepare.connect("clicked", prepare_ai)' in source
    assert 'ai_send.connect("clicked", send_ai)' in source
    assert 'ai_cancel.connect("clicked", cancel_ai)' in source
    assert 'if ai_preview is None:' in source
    assert 'analyze_with_consent(' in source
    # A page build must never send the prompt until a distinct button click.
    build = _build()
    send = next(node for node in ast.walk(build)
                if isinstance(node, ast.FunctionDef) and node.name == "send_ai")
    assert any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
               and node.func.id == "analyze_with_consent"
               for node in ast.walk(send))


def test_1009_remote_analysis_requires_preview_and_explicit_consent():
    source = _source().casefold()
    assert "prévisualis" in source or "aperçu" in source
    assert "consent" in source or "confirmer" in source
    assert "annul" in source, "User must be able to cancel before sending"


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
