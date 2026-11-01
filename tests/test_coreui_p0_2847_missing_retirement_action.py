"""#2847: CoreUI historical-placement retirement is explicit, scoped, no-delete.

The GTK/XTEST operator walkthrough remains a separate UX acceptance.
These tests hold the routing contract between GTK and the private Core
operator, without fabricating a proof of a real GTK run.
"""
from __future__ import annotations

import ast
from pathlib import Path


SOURCE = (Path(__file__).resolve().parents[1]
          / "src/cifereca/gui/library_inspection_view.py")


def _source():
    text = SOURCE.read_text(encoding="utf-8")
    ast.parse(text)
    return text


def test_missing_sqlite_retirement_has_separate_visible_preview_and_consent():
    source = _source()
    assert "Gtk.Button(label='Prévisualiser le retrait SQL…')" in source
    assert "Gtk.Button(label='Confirmer le retrait SQL…')" in source
    assert "missing_actions_scroll.set_visible(False)" in source
    assert "'stored_path_missing_historical' in case.states" in source
    assert "summary.get('status') == 'complete'" in source
    assert "missing_review.connect('clicked', preview_missing_retirement)" in source
    assert "missing_apply.connect('clicked', confirm_missing_retirement)" in source
    assert "session.preview_missing_retirement(chosen)" in source
    assert "result.get('requires_explicit_confirmation') is True" in source
    assert "'Retirer cet emplacement SQLite ?'" in source
    assert "if response != 'apply':" in source
    assert "session.confirm_missing_repair(" in source
    assert "chosen, user_confirmed=True," in source
    assert "dialog.set_default_response('cancel')" in source
    assert "dialog.set_close_response('cancel')" in source
    assert "session.cancel_missing_repair()" in source
    assert "missing_version[0] += 1" in source
    assert "version != missing_version[0]" in source


def test_preview_and_commit_use_existing_async_maintenance_worker_and_restart():
    source = _source()
    assert "'Prévisualisation du placement obsolète', read, completed, failed" in source
    assert "'Retrait contrôlé du placement obsolète'," in source
    assert "kind='maintenance-inspection'" in source
    assert "outcome.get('status') == 'retired'" in source
    assert "self.clear()" in source
    assert "self.start()" in source
    assert "session.revoke_manual_attachment_preview()" in source
    assert "reset_missing()" in source
    assert "missing_preview_ready[0] = False" in source
    # No private Core token, SQL mutation, physical unlink or provider call
    # should ever be introduced in a GTK signal path.
    for forbidden in (
        "plan_token", "DELETE FROM placements", ".unlink(",
        "os.remove(", "storage.placement_delete(", "retire_missing_placement(",
    ):
        assert forbidden not in source


def test_ui_does_not_make_sha_or_catalogue_summary_a_retirement_permit():
    source = _source()
    handler = source[source.index("def preview_missing_retirement(*_args):"):
                     source.index("def confirm_missing_retirement(*_args):")]
    assert "session.preview_missing_retirement(chosen)" in handler
    assert "result.get('status') == 'ready'" in handler
    assert "result.get('read_only') is True" in handler
    assert "result.get('requires_explicit_confirmation') is True" in handler
    assert "session.confirm_missing_repair(" not in handler
    confirm = source[source.index("def confirm_missing_retirement(*_args):"):
                     source.index("def render(*_args, reset=False):")]
    assert "Adw.MessageDialog.new" in confirm
    assert "session.confirm_missing_repair(" in confirm
    assert "user_confirmed=True" in confirm
