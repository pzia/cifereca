"""P0 #5048: reconciliation copy must not dispatch on a supplier name.

Only strings in GTK are changed; the Core owns proof, account, transaction
and one-shot undo decisions. The Paris read-only restriction is preserved.
"""
from __future__ import annotations

import ast
from pathlib import Path

VIEW = Path(__file__).resolve().parents[1] / "src/cifereca/gui/library_inspection_view.py"


def test_local_placement_confirmation_copy_is_provider_neutral():
    source = VIEW.read_text(encoding="utf-8")
    ast.parse(source)
    assert "Le Core revérifiera sous verrou les preuves du droit" in source
    assert "du fichier et du compte exact" in source
    assert "Placement local enregistré." in source
    assert "Annuler le placement local ?" in source

    # These former UI-only vendor branches must not come back. No branch is
    # introduced for a hypothetical new provider or untrusted item metadata.
    assert "'Placement local Kobo enregistré. '" not in source
    assert "'Placement local Numilog enregistré. '" not in source
    assert "'Annuler le placement Kobo ?'" not in source
    assert "'Annuler le placement Numilog ?'" not in source
    assert "if selected_provider[0] == 'kobo'" not in source

    # The selected pair goes through the Core preflight before any fresh
    # validation is offered. Inactive loans remain available for inspection.
    assert "session.preflight_manual_link(" in source
    assert "result.status == 'eligible_for_fresh_validation'" in source
    assert "result.read_only is True" in source
    assert "result.attachment_authorized is False" in source
    assert "'loan_inactive' in case.states" in source
    assert "'Emprunter'" not in source
