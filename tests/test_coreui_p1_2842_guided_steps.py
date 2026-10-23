"""#2842: the guided GTK labels never confer a reconciliation right.

Contract assertions complement native GTK/XTEST, which exercises actual
selection and consent. The stage guide must not introduce a second writer.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from cifereca.gui.library_inspection_view import _reconciliation_step_label


UI = Path(__file__).resolve().parents[1] / "src/cifereca/gui/library_inspection_view.py"


@pytest.mark.parametrize("stage, prefix", [
    ("idle", "1/4"), ("right", "2/4"), ("file", "3/4"),
    ("checking", "3/4"), ("blocked", "3/4"),
    ("ready", "4/4"), ("applying", "4/4"),
    ("done", "Placement enregistré"),
])
def test_guide_has_distinct_bounded_readonly_stages(stage, prefix):
    message = _reconciliation_step_label(stage)
    assert message.startswith(prefix)
    assert "PRIVATE" not in message
    assert len(message) < 130


@pytest.mark.parametrize("stage", ["file", "checking", "blocked", "ready"])
def test_paris_guidance_is_always_read_only(stage):
    label = _reconciliation_step_label(stage, "paris")
    assert "Paris" in label and "lecture seule" in label
    assert "sans placement" in label


def test_unknown_stage_cannot_render_worker_or_provider_private_text():
    assert _reconciliation_step_label("PRIVATE-auth-token", "PRIVATE-source") == (
        _reconciliation_step_label("idle")
    )


def test_real_selection_preflight_and_commit_drive_pinned_guide():
    code = UI.read_text(encoding="utf-8")
    ast.parse(code)
    assert "reconciliation-guided-step" in code
    assert "toolbar_view.add_bottom_bar(guide_status)" in code
    # The default filter is configured before handlers can cause a second
    # render, and every other state remains selectable from the dropdown.
    default = "state.set_selected(list(STATES).index('rights_without_verified_file'))"
    assert code.count(default) == 1
    assert code.index(default) < code.index("state.connect('notify::selected'")
    # Stage advances from an actual selected file to local proof evaluation,
    # through Core's fresh preview, to the SAME existing consent/commit.
    assert "guide('file' if file_key is not None" in code
    assert "guide('file' if fresh_preview.get_visible() else 'blocked')" in code
    assert "guide('checking')" in code
    assert "guide('ready' if status == 'preview' else 'blocked')" in code
    assert "guide('applying')" in code
    assert "guide('done')" in code
    assert "session.preflight_manual_link(" in code
    assert "session.preview_manual_attachment(" in code
    assert "session.confirm_manual_attachment(" in code
    assert "user_confirmed=True" in code
    assert code.count("Gtk.Button(label='Confirmer le placement local…')") == 1
