"""#2842: selected-pair UX steps are readable but never authorize a placement."""
from __future__ import annotations

from pathlib import Path

import pytest

from cifereca.gui.library_inspection_view import _guided_pair_step_text


@pytest.mark.parametrize(("stage", "indicator"), [
    ("right", "1/4"),
    ("file", "2/4"),
    ("proof", "3/4"),
    ("eligible", "3/4"),
    ("verifying", "3/4"),
    ("confirm", "4/4"),
    ("writing", "4/4"),
    ("blocked", "3/4"),
    ("done", "4/4"),
])
def test_guidance_is_closed_and_expresses_the_actual_progress(stage, indicator):
    label = _guided_pair_step_text(stage)
    assert label.startswith(indicator)
    assert 0 < len(label) <= 230
    assert "\\n" not in label
    assert "PRIVATE" not in label


def test_metadata_candidate_does_not_become_right_or_receipt():
    assert "ne prouve aucun droit" in _guided_pair_step_text("file")
    assert "non attestée" in _guided_pair_step_text("proof")
    assert "aucune écriture" in _guided_pair_step_text("verifying")
    assert "Placement non autorisé" in _guided_pair_step_text("blocked")
    assert "périmées" in _guided_pair_step_text("done")


@pytest.mark.parametrize("stage", [
    "file", "proof", "eligible", "verifying",
    "confirm", "writing", "blocked",
])
def test_paris_loan_is_never_presented_as_owned_or_mutable(stage):
    label = _guided_pair_step_text(stage, provider="paris")
    assert "consultation seule" in label
    assert "sans créer de placement" in label
    assert "transformer un prêt en achat" in label


def test_unknown_stages_are_not_rendered_from_raw_provider_or_errors():
    assert _guided_pair_step_text("PRIVATE-provider-response") == (
        _guided_pair_step_text("right")
    )
    assert _guided_pair_step_text("proof", provider="PRIVATE-profile") == (
        _guided_pair_step_text("proof")
    )


def test_footer_controls_and_starting_scope_remain_single_writer():
    view = (Path(__file__).resolve().parents[1]
            / "src/cifereca/gui/library_inspection_view.py").read_text()
    assert "state.set_selected(list(STATES).index('rights_without_verified_file'))" in view
    assert view.count("toolbar_view.add_bottom_bar(guide_status)") == 1
    assert view.count("guide_status.set_name('reconciliation-guided-stage')") == 1
    assert "toolbar_view.add_bottom_bar(action_scroll)" in view
    assert "toolbar_view.add_bottom_bar(footer)" in view
    assert view.count("def guide(stage):") == 1
    assert view.count("guide('done')") == 1
    assert "guide('confirm' if status == 'preview' else 'blocked')" in view
    assert "guide('eligible' if fresh_preview.get_visible() else 'blocked')" in view
    assert "guide('verifying')" in view
    assert "guide('writing')" in view
    assert "guide('blocked')" in view


def test_stage_four_never_claims_consent_before_human_apply():
    view = (Path(__file__).resolve().parents[1]
            / "src/cifereca/gui/library_inspection_view.py").read_text()
    consent = view.split("        def confirm_local_placement(*_args):", 1)[1]
    consent = consent.split("        def undo_local_placement(", 1)[0]
    assert consent.count("guide('writing')") == 1
    assert consent.index("dialog = Adw.MessageDialog.new(") < consent.index(
        "if response != 'apply':"
    ) < consent.index("guide('writing')")
    assert consent.index("guide('writing')") < consent.index(
        "def commit():"
    )
