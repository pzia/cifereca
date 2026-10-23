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
