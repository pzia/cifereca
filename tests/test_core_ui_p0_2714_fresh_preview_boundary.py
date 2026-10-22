"""P0 #2714: fresh private preview is not an implicit attach operation."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VIEW = ROOT / "src/cifereca/gui/library_inspection_view.py"
E2E = ROOT / "e2e/library_inspection_acceptance.py"


def test_fresh_preview_is_a_distinct_explicit_button_never_automatic():
    source = VIEW.read_text(encoding="utf-8")
    assert "Gtk.Button(label='Préparer le rattachement')" in source
    assert "Gtk.Button(label='Vérifier les preuves actuelles…')" in source
    assert "fresh_preview.set_visible(False)" in source
    assert "result.status == 'eligible_for_fresh_validation'" in source
    assert "session.preview_manual_attachment(" in source
    assert "self.parent.runner.submit(" in source
    assert "'Vérification locale avant rattachement', read, completed, failed" in source
    # Preview itself stays no-write. The separate confirm path must require
    # a human click, a consent response, and the private Core session token.
    assert "pair_confirm.connect('clicked', confirm_local_placement)" in source
    assert "if response != 'apply':" in source
    assert "session.confirm_manual_attachment(" in source
    assert "user_confirmed=True" in source
    assert "reconcile_numilog_manual(" not in source


def test_selection_and_context_change_cancel_in_flight_preview():
    source = VIEW.read_text(encoding="utf-8")
    assert "pair_version[0] += 1" in source
    assert "session.revoke_manual_attachment_preview()" in source
    assert "pair_version[0] != version or not session.valid()" in source
    assert "version != pair_version[0]" in source
    assert "selected_right[0] != chosen_right" in source
    assert "selected_file[0] != chosen_file" in source
    assert "plan_token" not in source


def test_gtk_numilog_right_search_by_notice_isbn_then_restores_full_list():
    probe = E2E.read_text(encoding="utf-8")
    assert "P0_2714_gtk_numilog_right_search_by_private_isbn" in probe
    assert "rights_search.set_text('9780306406157')" in probe
    assert "len([" in probe and "]) == 50" in probe
    core = (ROOT / "src/cifereca/core/library_local_inspection.py").read_text(
        encoding="utf-8"
    )
    assert "self._case_search.clear()" in core
    assert "self._case_search[f'right:{index}']" in core


def test_gtk_synthetic_missing_receipt_rejects_preview_button():
    probe = E2E.read_text(encoding="utf-8")
    assert "w.get_label()=='Vérifier les preuves actuelles…'" in probe
    assert "assert not fresh.get_visible() and not fresh.get_mapped()" in probe
    assert "P0_2714_pair_preflight_no_write_click" in probe
