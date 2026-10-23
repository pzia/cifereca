"""#2842: closed, non-authorizing right-first UX instructions.

These tests do not create a right, a download, a receipt or a placement.
The GTK/XTEST acceptance separately checks actual widget state.
"""
from __future__ import annotations

import inspect

from cifereca.gui.library_inspection_view import (
    LocalInspectionController, _guided_pair_instruction,
)


def test_local_reconciliation_guide_is_honest_at_all_four_steps():
    first = _guided_pair_instruction(right_selected=False, file_selected=False)
    second = _guided_pair_instruction(right_selected=True, file_selected=False)
    third = _guided_pair_instruction(right_selected=True, file_selected=True)
    fourth = _guided_pair_instruction(
        right_selected=True, file_selected=True, confirmation_available=True,
    )
    assert first.startswith("1/4") and "droit" in first
    assert second.startswith("2/4") and "fichier" in second
    assert "ne prouve aucun droit" in second
    assert third.startswith("3/4") and "sans reçu" in third
    assert "preuves fournisseur" in third
    assert fourth.startswith("4/4")
    assert "facultative" in fourth and "revérifiera" in fourth
    # A stale confirmation cannot be represented without both selections.
    assert _guided_pair_instruction(
        right_selected=False, file_selected=True, confirmation_available=True,
    ) == first
    assert _guided_pair_instruction(
        right_selected=True, file_selected=False, confirmation_available=True,
    ) == second


def test_reconciliation_controller_wires_initial_filter_and_stale_reset():
    source = inspect.getsource(LocalInspectionController.present)
    assert "state.set_selected(list(STATES).index('rights_without_verified_file'))" in source
    assert "guidance.set_name('reconciliation-guided-stage')" in source
    assert "guidance.set_text(_guided_pair_instruction(" in source
    assert "confirmation_available=status == 'preview'" in source
    assert source.index("state.set_selected(list(STATES).index(") < source.index(
        "state.connect('notify::selected'"
    )
