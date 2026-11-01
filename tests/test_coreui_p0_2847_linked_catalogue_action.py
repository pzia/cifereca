"""#2847: GTK sends a separate, explicit consent for catalogue detachment.

These static contracts complement, rather than impersonate, real GTK/XTEST
interaction and the SQLite/FS transaction tests of the underlying Core.
"""
from __future__ import annotations

import ast
from pathlib import Path


SOURCE = (
    Path(__file__).resolve().parents[1]
    / "src/cifereca/gui/library_inspection_view.py"
)


def _source():
    text = SOURCE.read_text(encoding="utf-8")
    ast.parse(text)
    return text


def test_linked_catalogue_action_is_separate_from_orphan_retirement():
    s = _source()
    assert "label='Prévisualiser le détachement des notices SQL…'" in s
    assert "label='Confirmer le détachement SQL…'" in s
    assert "missing_linked_review.connect('clicked', preview_missing_linked_retirement)" in s
    assert "missing_linked_apply.connect('clicked', confirm_missing_linked_retirement)" in s
    linked = s[s.index("def preview_missing_linked_retirement(*_args):"):
               s.index("def confirm_missing_linked_retirement(*_args):")]
    assert "session.preview_missing_linked_retirement(chosen)" in linked
    assert "result.get('status') == 'review_required'" in linked
    assert "catalogue_detachment_requires_confirmation" in linked
    assert "result.get('read_only') is True" in linked
    assert "result.get('requires_explicit_confirmation') is True" in linked
    assert "1 <= count <= 128" in linked
    assert "missing_linked_apply.set_visible(ready)" in linked
    assert "session.confirm_missing_repair(" not in linked


def test_linked_confirm_is_explicit_then_result_scope_checked():
    s = _source()
    linked = s[s.index("def confirm_missing_linked_retirement(*_args):"):
               s.index("def preview_missing_retirement(*_args):")]
    assert "Adw.MessageDialog.new" in linked
    assert "'Détacher les notices et retirer ce placement SQL ?'" in linked
    assert "dialog.set_default_response('cancel')" in linked
    assert "dialog.set_close_response('cancel')" in linked
    assert "if response != 'apply':" in linked
    assert "session.cancel_missing_repair()" in linked
    assert "session.confirm_missing_repair(" in linked
    assert "chosen, user_confirmed=True" in linked
    assert "outcome.get('reason') == 'catalogue_detached'" in linked
    assert "self.clear()" in linked and "self.start()" in linked
    for forbidden in (
        "plan_token", "DELETE FROM placements", ".unlink(",
        "os.remove(", "retire_missing_linked_catalogue(",
    ):
        assert forbidden not in s


def test_selection_change_revokes_linked_plan_and_disables_action():
    s = _source()
    assert "missing_linked_ready[0] = False" in s
    assert "missing_linked_apply.set_visible(False)" in s
    assert "missing_linked_apply.set_sensitive(False)" in s
    assert "missing_linked_review.set_sensitive(False)" in s
    assert "session.cancel_missing_repair()" in s
    assert "missing_version[0] += 1" in s
    assert "version != missing_version[0]" in s
    assert "summary.get('status') == 'complete'" in s
    assert "'stored_path_missing_historical' in case.states" in s
