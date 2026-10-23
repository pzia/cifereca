"""P1 #2842: a private history must not masquerade as an attested link."""
from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VIEW = ROOT / 'src/cifereca/gui/library_inspection_view.py'
HISTORY = ROOT / 'src/cifereca/gui/library_declaration_history.py'
CORE = ROOT / 'src/cifereca/core/library_local_inspection.py'


def test_private_history_is_opt_in_and_does_not_change_attested_stats():
    view = VIEW.read_text(encoding='utf-8')
    history = HISTORY.read_text(encoding='utf-8')
    core = CORE.read_text(encoding='utf-8')
    for text in (view, history, core):
        ast.parse(text)
    assert "box.append(declaration_history.expander)" in view
    assert "declaration_history = PrivateDeclarationHistory(" in view
    assert "self.expander.set_expanded(False)" in history
    assert "self.session.declared_links_page(" in history
    assert "include_revoked=self.include_revoked.get_active()" in history
    assert "if not self.alive() or not self.session.valid():" in history
    assert "self._clear()" in history
    assert "session.confirm_declared_link(" not in history
    assert "session.confirm_manual_attachment(" not in history
    assert "def declared_links_page(" in core
    assert "list_declared_links(" in core
    assert "if not self.valid() or self._manual_service is None:" in core
    assert "return page if self.valid() else None" in core


def test_history_only_uses_safe_source_and_review_status_vocabulary():
    from cifereca.gui.library_declaration_history import (
        _REVIEW_LABELS, _SOURCE_LABELS,
    )

    assert set(_SOURCE_LABELS) == {'kobo', 'numilog', 'paris'}
    assert set(_REVIEW_LABELS) == {
        'recorded_unverified_file', 'source_or_right_changed', 'revoked',
    }
    assert 'non revérifiés' in _REVIEW_LABELS['recorded_unverified_file']
    assert 'révoqué' in _REVIEW_LABELS['revoked'].casefold()


def test_stale_revocation_is_explicit_and_core_bound_without_gtk_worker_reads():
    history = HISTORY.read_text(encoding='utf-8')
    view = VIEW.read_text(encoding='utf-8')
    core = CORE.read_text(encoding='utf-8')
    assert "self.withdraw_button = Gtk.Button(label='Révoquer cette déclaration…')" in history
    assert "self.withdraw_button.set_sensitive(item.state == 'active')" in history
    assert "self.session.preview_history_withdrawal(" in history
    assert "self.session.confirm_history_withdrawal(" in history
    assert "user_confirmed=True" in history
    assert "dialog.set_body_use_markup(False)" in history
    assert "dialog.set_default_response('cancel')" in history
    assert "dialog.set_close_response('cancel')" in history
    assert "cancelled=lambda: self.generation != generation" in history
    assert "cancelled=lambda: not self._current" not in history
    assert "withdraw_declared_link(" not in history
    assert "def preview_history_withdrawal(" in core
    assert "def confirm_history_withdrawal(" in core
    assert "self._history_withdraw_pending = None" in core
    assert "Adw=Adw, window=window, runner=self.parent.runner" in view
