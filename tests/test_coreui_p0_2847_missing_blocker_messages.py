"""#2847: safe, useful UI wording for a retirement blocked by SQLite links.

Test the actual production function through AST isolation, so this wording
can be tested without a GTK runtime. Physical GTK behavior remains an E2E gate.
"""
from __future__ import annotations

import ast
from pathlib import Path


SOURCE = (
    Path(__file__).resolve().parents[1]
    / "src/cifereca/gui/library_inspection_view.py"
)


def _render(result):
    code = SOURCE.read_text(encoding="utf-8")
    module = ast.parse(code)
    node, = (
        item for item in module.body
        if isinstance(item, ast.FunctionDef)
        and item.name == "_missing_placement_retirement_message"
    )
    scope = {}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(SOURCE), "exec"), scope)
    return scope[node.name](result)


def test_catalogue_and_backup_counts_are_bounded_diagnostic_not_consent():
    message = _render({
        "status": "blocked", "reason": "dependent_references",
        "catalogue_rows": 2, "backup_rows": 1,
        "path": "/secret-user/ebook.epub",
        "credential_id": "SECRET",
        "plan_token": "NOT_TO_BE_EXPOSED",
    })
    assert "2 notice(s)" in message and "1 sauvegarde(s)" in message
    assert "D’autres références peuvent exister" in message
    for secret in ("/secret-user", "SECRET", "NOT_TO_BE_EXPOSED"):
        assert secret not in message
    assert "Aucune suppression" in message


def test_zero_direct_links_still_blocks_opaque_checkpoint_id_only():
    message = _render({
        "status": "blocked", "reason": "dependent_references",
        "catalogue_rows": 0, "backup_rows": 0,
    })
    assert "Retrait bloqué" in message
    assert "checkpoint" in message
    assert "Aucune notice liée" in message
    assert "confirmation requise" not in message


def test_ready_is_not_inferred_from_counts_without_core_ready_status():
    result = {
        "status": "blocked", "reason": "dependent_references",
        "catalogue_rows": 0, "backup_rows": 0,
    }
    assert "confirmation requise" not in _render(result)
    result["status"] = "ready"
    assert "confirmation requise" in _render(result)


def test_untrusted_or_unbounded_counts_never_enter_gtk_string():
    for value in (-1, 129, True, "3", None):
        message = _render({
            "status": "blocked", "reason": "dependent_references",
            "catalogue_rows": value, "backup_rows": 0,
        })
        assert message == 'Retrait bloqué : références non classifiables à examiner.'
    assert "privately-injected" not in _render({
        "status": "blocked", "reason": "privately-injected",
        "catalogue_rows": 0, "backup_rows": 0,
    })


def test_production_coreui_only_renders_sanitized_diagnostic():
    text = SOURCE.read_text(encoding="utf-8")
    ast.parse(text)
    handler = text[text.index("def preview_missing_retirement(*_args):"):
                   text.index("def confirm_missing_retirement(*_args):")]
    assert "session.preview_missing_retirement(chosen)" in handler
    assert "_missing_placement_retirement_message(result)" in handler
    assert "missing_note.set_tooltip_text(diagnostic)" in handler
    assert "missing_apply.set_visible(ready)" in handler
    assert "result.get('requires_explicit_confirmation') is True" in handler
    assert "session.confirm_missing_repair(" not in handler
