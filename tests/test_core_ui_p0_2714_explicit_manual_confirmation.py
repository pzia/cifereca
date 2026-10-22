"""#2714: confirmation GUI requires a second physical action, never auto-writes."""
from pathlib import Path


def test_gtk_explicit_placement_requires_dialog_and_private_session_token():
    root = Path(__file__).resolve().parents[1]
    source = (root / "src/cifereca/gui/library_inspection_view.py").read_text(
        encoding="utf-8",
    )
    assert "Gtk.Button(label='Confirmer le placement local…')" in source
    assert "pair_confirm.set_visible(False)" in source
    assert "pair_confirm.set_visible(status == 'preview')" in source
    assert "pair_confirm.connect('clicked', confirm_local_placement)" in source
    assert "dialog.add_response('cancel', 'Annuler')" in source
    assert "dialog.add_response('apply', 'Confirmer le placement')" in source
    assert "dialog.set_close_response('cancel')" in source
    assert "if response != 'apply':" in source
    assert "reset_pair(chosen_file)" in source
    assert "user_confirmed=True" in source
    assert "session.confirm_manual_attachment(" in source
    assert "pair_version[0] != version" in source
    assert "self.parent.runner.submit(" in source
    assert "'Confirmation du placement local'" in source
    assert "self.clear()  # retire stale private audit/cases" not in source
    # No private operation/reference/confirmation token is ever unpacked in GTK.
    assert "plan_token" not in source
    assert "operation_id" not in source
    assert "confirmation=" not in source
    assert "acquisition_lock_file" not in source
    assert "placement_put" not in source
    assert "db.execute(" not in source


def test_e2e_unauthenticated_candidate_hides_confirmation_button():
    root = Path(__file__).resolve().parents[1]
    source = (root / "e2e/library_inspection_acceptance.py").read_text(
        encoding="utf-8",
    )
    assert "w.get_label()=='Confirmer le placement local…'" in source
    assert "assert not confirm.get_visible() and not confirm.get_sensitive()" in source


def test_undo_is_only_possible_after_same_session_applied_placement():
    root = Path(__file__).resolve().parents[1]
    source = (root / "src/cifereca/gui/library_inspection_view.py").read_text(
        encoding="utf-8",
    )
    assert "Gtk.Button(label='Annuler le dernier placement…')" in source
    assert "pair_undo.set_visible(False)" in source
    assert "placement_committed = [False]" in source
    assert "if status == 'applied':" in source
    assert "placement_committed[0] = True" in source
    assert "outcome.get('undo_available') is True" in source
    assert "pair_undo.set_visible(can_undo)" in source
    assert "pair_undo.connect('clicked', undo_local_placement)" in source
    assert "session.preview_manual_undo(" in source
    assert "session.confirm_manual_undo(" in source
    assert "user_confirmed=True" in source
    assert "dialog.add_response('cancel', 'Conserver le placement')" in source
    assert "dialog.add_response('undo', 'Confirmer l’annulation')" in source
    assert "dialog.set_close_response('cancel')" in source
    assert "if response != 'undo':" in source
    assert "session.revoke_manual_attachment_preview()" in source
    assert "placement_committed[0] is not True" in source
    assert "Aucune suppression confirmée" in source
    # An undo token or journal identifier cannot be forged via GTK.
    assert "operation_id" not in source
    assert "plan_token" not in source
    assert "placement_delete" not in source
    assert "db.execute(" not in source


def test_gtk_incomplete_synthetic_audit_cannot_offer_undo():
    root = Path(__file__).resolve().parents[1]
    probe = (root / "e2e/library_inspection_acceptance.py").read_text(
        encoding="utf-8",
    )
    assert "w.get_label()=='Annuler le dernier placement…'" in probe
    assert "assert not undo.get_visible() and not undo.get_sensitive()" in probe
