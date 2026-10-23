"""#2842: four-step UI guidance never manufactures a Core write permission.

The GTK/XTEST synthetic selected-pair scenario validates physical mapping.
These tests enforce the stable, closed wording and callback boundaries.
"""
from __future__ import annotations

import ast
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[1] / "src/cifereca/gui/library_inspection_view.py"


def test_guided_stage_bar_is_pinned_and_presented_without_new_action():
    src = SOURCE.read_text(encoding="utf-8")
    ast.parse(src)
    assert "stage_label.set_name('reconciliation-guided-stages')" in src
    assert "toolbar_view.add_bottom_bar(stage_label)" in src
    assert src.index("toolbar_view.add_bottom_bar(stage_label)") < src.index(
        "toolbar_view.add_bottom_bar(action_scroll)"
    )
    assert "toolbar_view.add_bottom_bar(footer)" in src
    assert "stage_label.set_wrap(True)" in src
    # Stage labels are pure presentation; no Core mutation or second writer.
    stage = src.split("        def show_stage(", 1)[1].split(
        "        def reset_pair(", 1
    )[0]
    assert "stage_label.set_text(" in stage
    assert "session." not in stage
    assert "commit" not in stage.split("stage_label.set_text(")[0]
    for private in ("case.label", "case.details", "item_id", "account_id", "file_path"):
        assert private not in stage


def test_stage_advances_only_after_proven_preflight_preview_and_commit():
    src = SOURCE.read_text(encoding="utf-8")
    reset = src.split("        def reset_pair(", 1)[1].split(
        "        def valid(", 1
    )[0]
    assert "session.revoke_manual_attachment_preview()" in reset
    assert "show_stage()" in reset
    preflight = src.split("        def prepare_pair(", 1)[1].split(
        "        def preview_fresh_pair(", 1
    )[0]
    assert "result.status == 'eligible_for_fresh_validation'" in preflight
    assert "decision = 'preview_needed'" in preflight
    assert "show_stage(decision)" in preflight
    preview = src.split("        def preview_fresh_pair(", 1)[1].split(
        "        def confirm_local_placement(", 1
    )[0]
    assert "show_stage('checking')" in preview
    assert "show_stage('ready' if status == 'preview' else 'blocked')" in preview
    confirm = src.split("        def confirm_local_placement(", 1)[1].split(
        "        def undo_local_placement(", 1
    )[0]
    assert "show_stage('committing')" in confirm
    assert "if status == 'applied':" in confirm
    assert "show_stage('committed')" in confirm
    assert "reset_pair(chosen_file)" in confirm
