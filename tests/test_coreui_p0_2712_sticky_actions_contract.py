"""P0 #2712: pairing commands remain visible above the scrolling evidence.

Source contract complements physical XTEST: GUI actions must remain single
callbacks; no cloned button may bypass preflight or explicit user consent.
"""
from __future__ import annotations

import ast
from pathlib import Path


UI = Path(__file__).resolve().parents[1] / "src/cifereca/gui/library_inspection_view.py"


def test_four_pair_actions_are_pinned_to_the_bottom_bar_not_the_long_page():
    code = UI.read_text(encoding="utf-8")
    ast.parse(code)
    assert "reconciliation-content-scroll" in code
    assert "reconciliation-pair-actions-scroll" in code
    for button in ("pair_prepare", "fresh_preview", "pair_confirm", "pair_undo"):
        assert f"candidate_box.append({button})" not in code
        assert f"{button}.connect('clicked'," in code
    assert "pair_actions.append(action)" in code
    assert "for action in (pair_prepare, fresh_preview, pair_confirm, pair_undo):" in code
    # Manual exploration must be reachable even when the local evidence
    # panel is tall and the audit has no bibliographic candidate.
    assert "candidate_box.append(manual_toggle)" not in code
    assert "pair_actions.append(manual_toggle)" in code
    assert "manual_toggle.connect('clicked'," in code
    assert code.count("Gtk.Button(label='Comparer tous les fichiers non liés')") == 1
    assert "toolbar_view.add_bottom_bar(action_scroll)" in code
    assert "toolbar_view.add_bottom_bar(footer)" in code
    assert "action_scroll.set_visible(selected_right[0] is not None)" in code
    assert "action_scroll.set_visible(False)" in code
    # Widgets are not duplicated or rebound to a new, unauthenticated writer.
    assert code.count("Gtk.Button(label='Préparer le rattachement')") == 1
    assert code.count("Gtk.Button(label='Confirmer le placement local…')") == 1
    assert "session.preview_manual_attachment(" in code
    assert "session.confirm_manual_attachment(" in code
    assert "user_confirmed=True" in code


def test_declared_link_actions_share_pinned_footer_without_second_writer():
    """An existing local book can be declared without opening a tall pane."""
    code = UI.read_text(encoding="utf-8")
    actions = (UI.parent / "library_declaration_actions.py").read_text(encoding="utf-8")
    ast.parse(code)
    ast.parse(actions)
    for control in ("declare", "revoke"):
        assert f"body.append(self.{control})" not in actions
        assert actions.count(f"self.{control} = Gtk.Button(") == 1
    assert "pair_actions.prepend(declarations.declare)" in code
    assert "pair_actions.insert_child_after(declarations.revoke, declarations.declare)" in code
    assert "candidate_box.insert_child_after(declarations.expander, pair_report)" in code
    assert "body.append(self.status)" in actions
    assert "self.declare.connect('clicked', lambda *_: self.preview(False))" in actions
    assert "self.revoke.connect('clicked', lambda *_: self.preview(True))" in actions
    # No UI duplication that would bypass the revocable Core selection.
    assert code.count("pair_actions.prepend(declarations.declare)") == 1
    assert code.count("pair_actions.insert_child_after(declarations.revoke, declarations.declare)") == 1
    assert "session.confirm_declared_link(" in actions
    assert "user_confirmed=True" in actions
