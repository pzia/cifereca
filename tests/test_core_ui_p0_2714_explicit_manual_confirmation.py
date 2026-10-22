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
